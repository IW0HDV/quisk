/*
 * Sound modules that do not depend on alsa or portaudio
*/
#include <Python.h>
#include <complex.h>
#include <math.h>
#include <sys/time.h>
#include <time.h>

#ifdef MS_WINDOWS
#include <Winsock2.h>
#else
#include <sys/socket.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#endif

#include "quisk.h"
#include "filter.h"

// Thanks to Franco Spinelli for this fix:
// The H101 hardware using the PCM2904 chip has a one-sample delay between
// channels that must be fixed in software.  If you have this problem,
// set channel_delay in your config file.  The FIX_H101 #define is obsolete
// but still works.  It is equivalent to channel_delay = channel_q.

// The structure sound_dev represents a sound device to open.  If portaudio_index
// is -1, it is an ALSA sound device; otherwise it is a portaudio device with that
// index.  Portaudio devices have names that start with "portaudio".  A device name
// can be the null string, meaning the device should not be opened.  The sound_dev
// "handle" is either an alsa handle or a portaudio stream if the stream is open;
// otherwise it is NULL for a closed device.

// Set DEBUG_MIC (in quisk.h) to send the microphone samples to the FFT instead of the radio samples.
// The sample rate and mic sample rate must be 48000.  Use -c n2adr/conf4.py.
// 0: Normal operation.
// 1: Send filtered mic output to the FFT.
// 2: Send mic playback to the FFT and to the radio sound playback device "Playback".
// 3: Send unfiltered mic output to the FFT.

#if DEBUG_IO
static int debug_timer = 1;		// count up number of samples
#endif

static struct sound_dev Capture, Playback, MicCapture, MicPlayback, DigitalInput, DigitalOutput, RawSamplePlayback;
struct sound_dev quisk_DigitalRx1Output;
// These are arrays of all capture and playback devices, and MUST end with NULL:
static struct sound_dev * CaptureDevices[] = {&Capture, &MicCapture, &DigitalInput, NULL};
static struct sound_dev * PlaybackDevices[] = {&Playback, &MicPlayback, &DigitalOutput, &RawSamplePlayback, &quisk_DigitalRx1Output, NULL};
static SOCKET radio_sound_socket = INVALID_SOCKET;		// send radio sound samples to a socket
static SOCKET radio_sound_mic_socket = INVALID_SOCKET;	// receive mic samples from a socket
static int radio_sound_nshorts;						// number of shorts (two bytes) to send
static int radio_sound_mic_nshorts;					// number of shorts (two bytes) to receive

struct sound_conf quisk_sound_state;	// Current sound status

struct wav_file {
	FILE * fp;
	char file_name[QUISK_PATH_SIZE];
	int enable;
	unsigned long samples;
};

static struct wav_file file_rec_audio, file_rec_samples, file_rec_mic;

static int file_record_button;			// the file record button is down

static double digital_output_level = 0.7;
static int dc_remove_bw=100;			// bandwidth of DC removal filter

static ty_sample_start pt_sample_start;
static ty_sample_stop  pt_sample_stop;
static ty_sample_read  pt_sample_read;
ty_sample_write quisk_pt_sample_write;

static complex double cSamples[SAMP_BUFFER_SIZE];			// Complex buffer for samples

#if 0
void quisk_sample_level(const char * msg, complex double * cSamples, int nSamples, double scale)
{
        static double time0 = 0;
        static double level = 0;
	static int count = 0;
        double d;
        int i;

	count += nSamples;
        for (i = 0; i < nSamples; i++) {
                d = cabs(cSamples[i]);
                if (level < d)
                        level = d;
        }
        if (QuiskTimeSec() - time0 > 0.1) {
                printf ("sample_level %s: %10.6lf count %8d\n", msg, level / scale, count);
                level = 0;
		count = 0;
                time0 = QuiskTimeSec();
        }
}
#endif

void ptimer(int counts)	// used for debugging
{	// print the number of counts per second
	static unsigned int calls=0, total=0;
	static time_t time0=0;
	time_t dt;

	if (time0 == 0) {
		time0 = (int)(QuiskTimeSec() * 1.e6);
		return;
	}
	total += counts;
	calls++;
	if (calls % 1000 == 0) {
		dt = (int)(QuiskTimeSec() * 1.e6) - time0;
		printf("ptimer: %d counts in %d microseconds %.3f counts/sec\n",
			total, (unsigned)dt, (double)total * 1E6 / dt); 
	}
}

static void delay_sample (struct sound_dev * dev, double * dSamp, int nSamples)
{	// Delay the I or Q data stream by one sample.
	// cSamples is double D[nSamples][2]
	double d;
	double * first, * last;

	if (nSamples < 1)
		return;
	if (dev->channel_Delay == dev->channel_I) {
		first = dSamp;
		last = dSamp + nSamples * 2 - 2;
	}
	else if (dev->channel_Delay == dev->channel_Q) {
		first = dSamp + 1;
		last = dSamp + nSamples * 2 - 1;
	}
	else {
		return;
	}
	d = dev->save_sample;
	dev->save_sample = *last;
	while (--nSamples) {
		*last = *(last - 2);
		last -= 2;
	}
	*first = d;
}

static void correct_sample (struct sound_dev * dev, complex double * cSamples, int nSamples)
{	// Correct the amplitude and phase
	int i;
	double re, im;

	if (dev->doAmplPhase) {				// amplitude and phase corrections
		for (i = 0; i < nSamples; i++) {
			re = creal(cSamples[i]);
			im = cimag(cSamples[i]);
			re = re * dev->AmPhAAAA;
			im = re * dev->AmPhCCCC + im * dev->AmPhDDDD;
			cSamples[i] = re + I * im;
		}
	}
}

static void DCremove(complex double * cSamples, int nSamples, int sample_rate, int key_state)
{
	int i;
	double omega, Qsin, Qcos, H0, x;
	complex double c;
	static int old_sample_rate = 0;
	static int old_bandwidth = 0;
	static double alpha = 0.95;
	static complex double dc_remove = 0;
	static complex double dc_average = 0;		// Average DC component in samples
	static complex double dc_sum = 0;
	static int dc_count = 0;
	static int dc_key_delay = 0;

	if (sample_rate != old_sample_rate || dc_remove_bw != old_bandwidth) {
		old_sample_rate = sample_rate;	// calculate a new alpha
		old_bandwidth = dc_remove_bw;
		if (old_bandwidth > 0) {
			omega = M_PI * old_bandwidth / (old_sample_rate / 2.0);
			Qsin = sin(omega);
			Qcos = cos(omega);
			H0 = 1.0 / sqrt(2.0);
			x = ((Qcos - 1) * (Qcos - 1) + Qsin * Qsin) / (H0 * H0) - Qsin * Qsin;
			x = sqrt(x);
			alpha = Qcos - x;
			//printf ("DC remove: alpha %.3f rate %i bw %i\n", alpha, old_sample_rate, old_bandwidth);
		}
		else {
			//printf("DC remove: disable\n");
		}
	}
	if (quisk_is_vna || old_bandwidth == 0) {
	}
	else if (old_bandwidth == 1) {
		if (key_state) {
			dc_key_delay = 0;
			dc_sum = 0;
			dc_count = 0;
		}
		else if (dc_key_delay < old_sample_rate) {
			dc_key_delay += nSamples;
		}
		else {
			dc_count += nSamples;
			for (i = 0; i < nSamples; i++)		// Correction for DC offset in samples
				dc_sum += cSamples[i];
			if (dc_count > old_sample_rate * 2) {
				dc_average = dc_sum / dc_count;
				//printf("dc average %lf   %lf %d\n", creal(dc_average), cimag(dc_average), dc_count);
				//printf("dc polar %.0lf   %d\n", cabs(dc_average),
			   			//	(int)(360.0 / 2 / M_PI * atan2(cimag(dc_average), creal(dc_average))));
				dc_sum = 0;
				dc_count = 0;
			}
		}
		for (i = 0; i < nSamples; i++)	// Correction for DC offset in samples
			cSamples[i] -= dc_average;
	}
	else if (old_bandwidth > 1) {
		for (i = 0; i < nSamples; i++) {	// DC removal; R.G. Lyons page 553; 3rd Ed. p 762
			c = cSamples[i] + dc_remove * alpha;
			cSamples[i] = c - dc_remove;
			dc_remove = c;
		}
	}
}

static void record_audio(struct wav_file * wavfile, complex double * cSamples, int nSamples)
{  // Record the speaker audio to a WAV file, PCM, 16 bits, one channel
   // TODO: correct for big-endian byte order
	FILE * fp;
	int j;
	short samp;			// must be 2 bytes
	unsigned int u;		// must be 4 bytes
	unsigned short s;	// must be 2 bytes

	switch (nSamples) {
	case -1:			// Open the file
		if (wavfile->fp)
			fclose(wavfile->fp);
		wavfile->fp = fp = fopen(wavfile->file_name, "wb");
		if ( ! fp) {
			wavfile->enable = 0;
			return;
		}
		if (fwrite("RIFF", 1, 4, fp) != 4) {
			fclose(fp);
			wavfile->fp = NULL;
			wavfile->enable = 0;
			return;
		}
		// pcm data, 16-bit samples, one channel
		u = 36;
		fwrite(&u, 4, 1, fp);
		fwrite("WAVE", 1, 4, fp);
		fwrite("fmt ", 1, 4, fp);
		u = 16;
		fwrite(&u, 4, 1, fp);
		s = 1;		// wave_format_pcm
		fwrite(&s, 2, 1, fp);
		s = 1;		// number of channels
		fwrite(&s, 2, 1, fp);
		u = Playback.sample_rate;	// sample rate
		fwrite(&u, 4, 1, fp);
		u *= 2;
		fwrite(&u, 4, 1, fp);
		s = 2; 
		fwrite(&s, 2, 1, fp);
		s = 16; 
		fwrite(&s, 2, 1, fp);
		fwrite("data", 1, 4, fp);
		u = 0;
		fwrite(&u, 4, 1, fp);
		wavfile->samples = 0;
		break;
	case -2:		// close the file
		if (wavfile->fp)
			fclose(wavfile->fp);
		wavfile->fp = NULL;
		break;
	default:		// write the sound data to the file
		fp = wavfile->fp;
		u = (unsigned int)nSamples;
		if (wavfile->samples >= 2147483629 - u) {	// limit size to 2**32 - 1
			wavfile->samples = ~0;
			u = ~0;
			fseek(fp, 40, SEEK_SET);	// seek from the beginning
			fwrite(&u, 4, 1, fp);
			fseek(fp, 4, SEEK_SET);
			fwrite(&u, 4, 1, fp);
		}
		else {
			wavfile->samples += u;
			fseek(fp, 40, SEEK_SET);
			u = 2 * wavfile->samples;
			fwrite(&u, 4, 1, fp);
			fseek(fp, 4, SEEK_SET);	
			u += 36;
			fwrite(&u, 4, 1, fp);
		}
		fseek(fp, 0, SEEK_END);		// seek to the end
		for (j = 0; j < nSamples; j++) {
			samp = (short)(creal(cSamples[j]) / 65536.0);
			fwrite(&samp, 2, 1, fp);
		}
		break;
	}
}

static int record_samples(struct wav_file * wavfile, complex double * cSamples, int nSamples)
{  // Record the samples to a WAV file, two float samples I/Q
	FILE * fp;	// TODO: correct for big-endian byte order
	int j;
	float samp;			// must be 4 bytes
	unsigned int u;		// must be 4 bytes
	unsigned short s;	// must be 2 bytes

	switch (nSamples) {
	case -1:			// Open the file
		if (wavfile->fp)
			fclose(wavfile->fp);
		wavfile->fp = fp = fopen(wavfile->file_name, "wb");
		if ( ! fp) {
			wavfile->enable = 0;
			return 0;
		}
		if (fwrite("RIFF", 1, 4, fp) != 4) {
			fclose(fp);
			wavfile->fp = NULL;
			wavfile->enable = 0;
			return 0;
		}
		// IEEE float data, two channels
		u = 36;
		fwrite(&u, 4, 1, fp);
		fwrite("WAVE", 1, 4, fp);
		fwrite("fmt ", 1, 4, fp);
		u = 16;
		fwrite(&u, 4, 1, fp);
		s = 3;		// wave_format_ieee_float
		fwrite(&s, 2, 1, fp);
		s = 2;		// number of channels
		fwrite(&s, 2, 1, fp);
		u = quisk_sound_state.sample_rate;	// sample rate
		fwrite(&u, 4, 1, fp);
		u *= 8;
		fwrite(&u, 4, 1, fp);
		s = 8; 
		fwrite(&s, 2, 1, fp);
		s = 32; 
		fwrite(&s, 2, 1, fp);
// Add a LIST chunk of type INFO for further metadata
		fwrite("data", 1, 4, fp);
		u = 0;
		fwrite(&u, 4, 1, fp);
		wavfile->samples = 0;
		break;
	case -2:	// close the file
		if (wavfile->fp)
			fclose(wavfile->fp);
		wavfile->fp = NULL;
		wavfile->enable = 0;
		break;
	default:	// write the sound data to the file
		fp = wavfile->fp;
		if ( ! fp)
			return 0;
		u = (unsigned int)nSamples;
		if (wavfile->samples >= 536870907 - u) {	// limit size to 2**32 - 1
			wavfile->samples = ~0;
			u = ~0;
			fseek(fp, 40, SEEK_SET);	// seek from the beginning
			fwrite(&u, 4, 1, fp);
			fseek(fp, 4, SEEK_SET);		// seek from the beginning
			fwrite(&u, 4, 1, fp);
		}
		else {
			wavfile->samples += u;
			fseek(fp, 40, SEEK_SET);	// seek from the beginning
			u = 8 * wavfile->samples;
			fwrite(&u, 4, 1, fp);
			fseek(fp, 4, SEEK_SET);		// seek from the beginning
			u += 36 ;
			fwrite(&u, 4, 1, fp);
		}
		fseek(fp, 0, SEEK_END);		// seek to the end
		for (j = 0; j < nSamples; j++) {
			samp = creal(cSamples[j]) / CLIP32;
			fwrite(&samp, 4, 1, fp);
			samp = cimag(cSamples[j]) / CLIP32;
			fwrite(&samp, 4, 1, fp);
		}
		break;
	}
	return 1;
}

void quisk_sample_source(ty_sample_start start, ty_sample_stop stop, ty_sample_read read)
{
	pt_sample_start = start;
	pt_sample_stop = stop;
	pt_sample_read = read;
}

void quisk_sample_source4(ty_sample_start start, ty_sample_stop stop, ty_sample_read read, ty_sample_write write)
{
	pt_sample_start = start;
	pt_sample_stop = stop;
	pt_sample_read = read;
	quisk_pt_sample_write = write;
}

/*!
 * \brief Driver interface for reading samples from a device
 * 
 * \param dev Input. Device to read from
 * \param cSamples Output. Read samples.
 * \returns number of samples read
 */
int read_sound_interface(
   struct sound_dev* dev,
   complex double * cSamples
)
{
   int i, nSamples;
   double avg, samp, re, im, frac, diff;
   
   // Read using correct driver.
   switch( dev->driver )
   {
      case DEV_DRIVER_PORTAUDIO:
         nSamples = quisk_read_portaudio(dev, cSamples);
         break;
      case DEV_DRIVER_ALSA:
         nSamples = quisk_read_alsa(dev, cSamples);
         break;
      case DEV_DRIVER_PULSEAUDIO:
         nSamples = quisk_read_pulseaudio(dev, cSamples);
         break;
      case DEV_DRIVER_NONE:
      default:
         return 0;
   }
   if ( ! cSamples || nSamples <= 0 || dev->sample_rate <= 0)		// cSamples can be NULL
      return nSamples;
   // Calculate average squared level
   avg = dev->average_square;
   frac = 1.0 / (0.2 * dev->sample_rate);
   for (i = 0; i < nSamples; i++) {
      re = creal(cSamples[i]);
      im = cimag(cSamples[i]);
      samp = re * re + im * im;
      diff = samp - avg;
      if (diff >= 0)
         avg = samp;	// set to peak value
      else
         avg = avg + frac * diff;
   }
   dev->average_square = avg;
   return nSamples;
}

/*!
 * \brief Driver interface for playing samples to a device
 * 
 * \param dev Input. Device to play to
 * \param nSamples Input. Number of samples to play
 * \param cSamples Input. Samples to play
 * \param report_latency Input. 1 to report latency, 0 otherwise.
 * \param volume Input. [0,1] volume ratio
 * \returns number of samples read
 */
void play_sound_interface(
   struct sound_dev* dev,
   int nSamples,
   complex double * cSamples,
   int report_latency,
   double volume
)
{
   int i;
   double avg, samp, re, im, frac, diff;

   if (cSamples && nSamples > 0 && dev->sample_rate > 0) {
      // Calculate average squared level
      avg = dev->average_square;
      frac = 1.0 / (0.2 * dev->sample_rate);
      for (i = 0; i < nSamples; i++) {
         re = creal(cSamples[i]);
         im = cimag(cSamples[i]);
         samp = re * re + im * im;
         diff = samp - avg;
         if (diff >= 0)
            avg = samp;	// set to peak value
         else
            avg = avg + frac * diff;
      }
      dev->average_square = avg;
   }
   // Play using correct driver.
   switch( dev->driver )
   {
      case DEV_DRIVER_PORTAUDIO:
         quisk_play_portaudio(dev, nSamples, cSamples, report_latency, volume);
         break;
      case DEV_DRIVER_ALSA:
         quisk_play_alsa(dev, nSamples, cSamples, report_latency, volume);
         break;
      case DEV_DRIVER_PULSEAUDIO:
         quisk_play_pulseaudio(dev, nSamples, cSamples, report_latency, volume);
         break;
      case DEV_DRIVER_NONE:
      default:
         break;
   }
}

static int read_radio_sound_socket(complex double * cSamples)
{
	int i, bytes, nSamples;
	short s;
	double d;
	struct timeval tm_wait;
	char buf[1500];
	fd_set fds;
	static int started = 0;

	nSamples = 0;
	while (1) {		// read all available blocks
		if (nSamples > SAMP_BUFFER_SIZE / 2)
			break;
		tm_wait.tv_sec = 0;
		tm_wait.tv_usec = 0;
		FD_ZERO (&fds);
		FD_SET (radio_sound_mic_socket, &fds);
		if (select (radio_sound_mic_socket + 1, &fds, NULL, NULL, &tm_wait) != 1)
			break;
		bytes = recv(radio_sound_mic_socket, buf, 1500,  0);
		if (bytes == radio_sound_mic_nshorts * 2) {		// required block size
			started = 1;
			for (i = 2; i < bytes; i += 2) {
				memcpy(&s, buf + i, 2);
				d = (double)s / CLIP16 * CLIP32;	// convert 16-bit samples to 32 bits
				cSamples[nSamples++] = d + I * d;
			}
		}
	}
	if ( ! started && nSamples == 0) {
		i = send(radio_sound_mic_socket, "rr", 2, 0);
		if (i != 2)
			printf("read_radio_sound_mic_socket returned %d\n", i);
	}
	return nSamples;
}

static void send_radio_sound_socket(complex double * cSamples, int count, double volume)
{	// Send count samples.  Each sample is sent as two shorts (4 bytes) of I/Q data.
	// Send an initial two bytes of zero for each block.
	// Transmission is delayed until a whole block of data is available.
	int i, sent;
	static short udp_iq[750] = {0};		// Documented maximum radio sound samples is 367
	static int udp_size = 1;

	for (i = 0; i < count; i++) {
		udp_iq[udp_size++] = (short)(creal(cSamples[i]) * volume * (double)CLIP16 / CLIP32);
		udp_iq[udp_size++] = (short)(cimag(cSamples[i]) * volume * (double)CLIP16 / CLIP32);
		if (udp_size >= radio_sound_nshorts) {	// check count
			sent = send(radio_sound_socket, (char *)udp_iq, udp_size * 2, 0);
			if (sent != udp_size * 2)
				printf("Send audio socket returned %d\n", sent);
			udp_size = 1;
		}
	}
}

int quisk_read_sound(void)	// Called from sound thread
{  // called in an infinite loop by the main program
	int i, nSamples, mic_count, mic_interp, retval, is_cw, mic_sample_rate;
	double mic_play_volume;
	complex double tx_mic_phase;
	static double cwEnvelope=0;
	static double cwCount=0;
	static complex double tuneVector = (double)CLIP32 / CLIP16;	// Convert 16-bit to 32-bit samples
	static struct quisk_cFilter filtInterp={NULL};
	int key_state, is_DGT;
#if DEBUG_MIC == 1
	complex double tmpSamples[SAMP_BUFFER_SIZE];
#endif

	quisk_sound_state.interupts++;
	key_state = quisk_is_key_down(); //reading this once is important for predicable bevavior on cork/flush
#if DEBUG_IO > 1
	QuiskPrintTime("Start read_sound", 0);
#endif

#ifndef MS_WINDOWS
	if (quisk_sound_state.IQ_server[0] && ! (rxMode == CWL || rxMode == CWU)) {
		if (Capture.handle && Capture.driver == DEV_DRIVER_PULSEAUDIO) {
			if (key_state == 1 && !Capture.cork_status)
			quisk_cork_pulseaudio(&Capture, 1);
			else if (key_state == 0 && Capture.cork_status) {
				quisk_cork_pulseaudio(&Capture, 0);
				quisk_flush_pulseaudio(&Capture);
			}
		}
		if (MicPlayback.handle && MicPlayback.driver == DEV_DRIVER_PULSEAUDIO) {
			if (key_state == 0 && !MicPlayback.cork_status)
			quisk_cork_pulseaudio(&MicPlayback, 1);
			else if (key_state == 1 && MicPlayback.cork_status) {
				quisk_cork_pulseaudio(&MicPlayback, 0);
				quisk_flush_pulseaudio(&MicPlayback);
			}
		}
	}
	else if (quisk_sound_state.IQ_server[0]) {
		if (Capture.handle && Capture.driver == DEV_DRIVER_PULSEAUDIO) {
			if (Capture.cork_status)
			quisk_cork_pulseaudio(&Capture, 0);
		}
		if (MicPlayback.handle && MicPlayback.driver == DEV_DRIVER_PULSEAUDIO) {
			if (MicPlayback.cork_status)
			quisk_cork_pulseaudio(&MicPlayback, 0);
		}
	}
#endif

	if (pt_sample_read) {			// read samples from SDR-IQ or UDP or SoapySDR
		nSamples = (*pt_sample_read)(cSamples);
		DCremove(cSamples, nSamples, quisk_sound_state.sample_rate, key_state);
		if (nSamples <= 0)
			QuiskSleepMicrosec(2000);
	}
	else if (Capture.handle) {		// blocking read from soundcard
		nSamples = read_sound_interface(&Capture, cSamples);
		if (Capture.channel_Delay >= 0)	// delay the I or Q channel by one sample
			delay_sample(&Capture, (double *)cSamples, nSamples);
		if (Capture.doAmplPhase)		// amplitude and phase corrections
			correct_sample(&Capture, cSamples, nSamples);
		DCremove(cSamples, nSamples, quisk_sound_state.sample_rate, key_state);
		if (nSamples <= 0)
			QuiskSleepMicrosec(2000);
	}
	else {
		QuiskSleepMicrosec(5000);
		nSamples = QuiskDeltaMsec(1) * quisk_sound_state.sample_rate / 1000;
		if (nSamples > SAMP_BUFFER_SIZE / 2)
			nSamples = SAMP_BUFFER_SIZE / 2;
		for (i = 0; i < nSamples; i++)
			cSamples[i] = 0;
	}
	retval = nSamples;		// retval remains the number of samples read
#if DEBUG_IO
	debug_timer += nSamples;
	if (debug_timer >= quisk_sound_state.sample_rate)		// one second
		debug_timer = 0;
#endif
#if DEBUG_IO > 2
	ptimer (nSamples);
#endif
	quisk_sound_state.latencyCapt = nSamples;	// samples available
#if DEBUG_IO > 1
	QuiskPrintTime("  read samples", 0);
#endif
	// Perhaps record the Rx samples to a file
	if ( ! key_state && file_rec_samples.fp)
		record_samples(&file_rec_samples, cSamples, nSamples);
	// Perhaps write samples to a loopback device for use by another program
	if (RawSamplePlayback.handle)
		play_sound_interface(&RawSamplePlayback, nSamples, cSamples, 0, 1.0);
	// Perhaps replace the samples with samples from a file
	if (quisk_record_state == PLAY_SAMPLES)
		quisk_play_samples(cSamples, nSamples);
#if ! DEBUG_MIC
	nSamples = quisk_process_samples(cSamples, nSamples);
#endif
#if DEBUG_IO > 1
	QuiskPrintTime("  process samples", 0);
#endif

	is_DGT = rxMode == DGT_U || rxMode == DGT_L || rxMode == DGT_IQ || rxMode == DGT_FM;
	if (quisk_record_state == PLAYBACK)
		quisk_tmp_playback(cSamples, nSamples, 1.0);		// replace radio sound
	else if (quisk_record_state == PLAY_FILE)
		quisk_file_playback(cSamples, nSamples, 1.0);		// replace radio sound
   
	// Play the demodulated audio
#if DEBUG_MIC != 2
	play_sound_interface(&Playback, nSamples, cSamples, 1, quisk_audioVolume);
#endif
	if (radio_sound_socket != INVALID_SOCKET)
		send_radio_sound_socket(cSamples, nSamples, quisk_audioVolume);
   
	// Play digital if required
	if (is_DGT)
		play_sound_interface(&DigitalOutput, nSamples, cSamples, 1, digital_output_level);
   
	// Perhaps record the speaker audio to a file
	if ( ! key_state && file_rec_audio.fp)
		record_audio(&file_rec_audio, cSamples, nSamples);   // Record Rx samples

#if DEBUG_IO > 1
	QuiskPrintTime("  play samples", 0);
#endif
	// Read and process the microphone
	mic_sample_rate = quisk_sound_state.mic_sample_rate;
	if (MicCapture.handle)
		mic_count = read_sound_interface(&MicCapture, cSamples);
	else if (radio_sound_mic_socket != INVALID_SOCKET)
		mic_count = read_radio_sound_socket(cSamples);
	else {      // No mic source; use zero samples
		mic_count = QuiskDeltaMsec(0) * mic_sample_rate / 1000;
		if (mic_count > SAMP_BUFFER_SIZE / 2)
			mic_count = SAMP_BUFFER_SIZE / 2;
		for (i = 0; i < mic_count; i++)
			cSamples[i] = 0;
	}
	if (quisk_record_state == PLAYBACK)			// Discard previous samples and replace with saved sound
		quisk_tmp_microphone(cSamples, mic_count);
	else if (quisk_record_state == PLAY_FILE)	// Discard previous samples and replace with saved sound
		quisk_file_microphone(cSamples, mic_count);
	if (DigitalInput.handle) {
		if (is_DGT) {		// Discard previous mic samples and use digital samples
			mic_sample_rate = DigitalInput.sample_rate;
			mic_count = read_sound_interface(&DigitalInput, cSamples);
		}
		else {		// Read and discard any digital samples
			read_sound_interface(&DigitalInput, NULL);
		}
	}
	else if (is_DGT) {	// Use zero-valued samples
		for (i = 0; i < mic_count; i++)
			cSamples[i] = 0;
	}
	//quisk_sample_level("read mic or DGT", cSamples, mic_count, CLIP16);
	// Perhaps record the microphone audio to the speaker audio file
	if (key_state && file_rec_audio.fp)
		record_audio(&file_rec_audio, cSamples, mic_count);
	// Perhaps record the microphone audio to the microphone audio file
	if (file_rec_mic.fp)
		record_audio(&file_rec_mic, cSamples, mic_count);
	if (mic_count > 0) {
#if DEBUG_IO > 1
		QuiskPrintTime("  mic-read", 0);
#endif
#if DEBUG_MIC == 3
		quisk_process_samples(cSamples, mic_count);
#endif
		// quisk_process_microphone returns samples at the sample rate MIC_OUT_RATE
		mic_count = quisk_process_microphone(mic_sample_rate, cSamples, mic_count);
#if DEBUG_MIC == 1
		for (i = 0; i < mic_count; i++)
			tmpSamples[i] = cSamples[i] * (double)CLIP32 / CLIP16;	// convert 16-bit samples to 32 bits
		quisk_process_samples(tmpSamples, mic_count);
#endif
#if DEBUG_IO > 1
		QuiskPrintTime("  mic-proc", 0);
#endif
	}
	//quisk_sample_level("quisk_process_microphone", cSamples, mic_count, CLIP16);
	// Mic playback without a mic is needed for CW
	if (MicPlayback.handle) {		// Mic playback: send mic I/Q samples to a sound card
		//quisk_sample_level("MicPlayback.handle", cSamples, mic_count, CLIP16);
		mic_play_volume = 1.0;
		if (rxMode == CWL || rxMode == CWU) {	// Transmit CW
			is_cw = 1;
		}
		else {
			is_cw = 0;
			cwCount = 0;
			cwEnvelope = 0.0;
		}
		tx_mic_phase = cexp(( -I * 2.0 * M_PI * quisk_tx_tune_freq) / MicPlayback.sample_rate);
		if (is_cw) {	// Transmit CW; use capture device for timing, not microphone
			cwCount += (double)retval * MicPlayback.sample_rate / quisk_sound_state.sample_rate;
			mic_count = 0;
			if (quisk_is_key_down()) {
				while (cwCount >= 1.0) {
					if (cwEnvelope < 1.0) {
						cwEnvelope += 1. / (MicPlayback.sample_rate * 5e-3);	// 5 milliseconds
						if (cwEnvelope > 1.0)
							cwEnvelope = 1.0;
					}
					if (quiskSpotLevel >= 0)
						cSamples[mic_count++] = (CLIP16 - 1) * cwEnvelope * quiskSpotLevel / 1000.0 * tuneVector * quisk_sound_state.mic_out_volume;
					else
						cSamples[mic_count++] = (CLIP16 - 1) * cwEnvelope * tuneVector * quisk_sound_state.mic_out_volume;
					tuneVector *= tx_mic_phase;
					cwCount -= 1;
				}
			}
			else {		// key is up
				while (cwCount >= 1.0) {
					if (cwEnvelope > 0.0) {
						cwEnvelope -= 1.0 / (MicPlayback.sample_rate * 5e-3);	// 5 milliseconds
						if (cwEnvelope < 0.0)
							cwEnvelope = 0.0;
					}
					cSamples[mic_count++] = (CLIP16 - 1) * cwEnvelope * tuneVector * quisk_sound_state.mic_out_volume;
					tuneVector *= tx_mic_phase;
					cwCount -= 1;
				}
			}
		}
		else if( ! DEBUG_MIC && ! quisk_is_key_down()) {	// Not CW and key up: zero samples
			mic_play_volume = 0.0;
			for (i = 0; i < mic_count; i++)
				cSamples[i] = 0.0;
		}
		// Perhaps interpolate the mic samples back to the mic play rate
		mic_interp = MicPlayback.sample_rate / MIC_OUT_RATE;
		if ( ! is_cw && mic_interp > 1) {
			if (! filtInterp.dCoefs)
				quisk_filt_cInit(&filtInterp, quiskFilt12_19Coefs, sizeof(quiskFilt12_19Coefs)/sizeof(double));
			mic_count = quisk_cInterpolate(cSamples, mic_count, &filtInterp, mic_interp);
		}
		// Tune the samples to frequency and convert 16-bit samples to 32-bits (using tuneVector)
		if ( ! is_cw) {
			for (i = 0; i < mic_count; i++) {
				cSamples[i] = conj(cSamples[i]) * tuneVector * quisk_sound_state.mic_out_volume;
				tuneVector *= tx_mic_phase;
			}
		}
		// delay the I or Q channel by one sample
		if (MicPlayback.channel_Delay >= 0)
			delay_sample(&MicPlayback, (double *)cSamples, mic_count);
		// amplitude and phase corrections
		if (MicPlayback.doAmplPhase)
			correct_sample (&MicPlayback, cSamples, mic_count);
		// play mic samples
		//quisk_sample_level("play MicPlayback", cSamples, mic_count, CLIP32);
		play_sound_interface(&MicPlayback, mic_count, cSamples, 1, mic_play_volume);
#if DEBUG_MIC == 2
		play_sound_interface(&Playback, mic_count, cSamples, 1, quisk_audioVolume);
		quisk_process_samples(cSamples, mic_count);
#endif
	}
#if DEBUG_IO > 1
	QuiskPrintTime("  finished", 0);
#endif
	// Return negative number for error
	return retval;
}

int quisk_get_overrange(void)	// Called from GUI thread
{  // Return the overrange (ADC clip) counter, then zero it
	int i;

	i = quisk_sound_state.overrange + Capture.overrange;
	quisk_sound_state.overrange = 0;
	Capture.overrange = 0;
	return i;
}

void quisk_close_sound(void)	// Called from sound thread
{
#ifdef MS_WINDOWS
	int cleanup = radio_sound_socket != INVALID_SOCKET || radio_sound_mic_socket != INVALID_SOCKET;
#endif
	quisk_close_sound_portaudio();
	quisk_close_sound_alsa(CaptureDevices, PlaybackDevices);
	quisk_close_sound_pulseaudio();
	if (pt_sample_stop)
		(*pt_sample_stop)();
	strncpy (quisk_sound_state.err_msg, CLOSED_TEXT, QUISK_SC_SIZE);
	if (radio_sound_socket != INVALID_SOCKET) {
		close(radio_sound_socket);
		radio_sound_socket = INVALID_SOCKET;
	}
	if (radio_sound_mic_socket != INVALID_SOCKET) {
		shutdown(radio_sound_mic_socket, QUISK_SHUT_RD);
		send(radio_sound_mic_socket, "ss", 2, 0);
		send(radio_sound_mic_socket, "ss", 2, 0);
		QuiskSleepMicrosec(1000000);
		close(radio_sound_mic_socket);
		radio_sound_mic_socket = INVALID_SOCKET;
	}
#ifdef MS_WINDOWS
	if (cleanup)
		WSACleanup();
#endif
}

static void set_num_channels(struct sound_dev * dev)
{	// Set num_channels to the maximum channel index plus one
	dev->num_channels = dev->channel_I;
	if (dev->num_channels < dev->channel_Q)
		dev->num_channels = dev->channel_Q;
	dev->num_channels++;
}

//! \brief Returns 1 if \c string starts with \c prefix. 0 otherwise.
int starts_with( const char* string, const char* prefix )
{
   size_t plen = strlen(prefix);
   if( strlen(string) < plen )
      return 0;
   else
      return strncmp( string, prefix, plen ) == 0 ? 1 : 0;
}

/*!
 * \brief From the sound_dev.name field, decide which driver to use for which device
 */
void decide_drivers(
	struct sound_dev** pDevs
)
{
	const char* name;
	// No name means no driver.
	// If name starts with 'portaudio', it's portaudio. Else, if it starts with
	// 'pulse', it's PulseAudio. Else, if it starts with 'alsa', it's ALSA.
	// Otherwise, just guess ALSA.
   
   while(1)
   {
      struct sound_dev* dev = *pDevs++;
      if( !dev )
         break;
      
      name = dev->name;
      if( ! name || name[0] == '\0' )
         dev->driver = DEV_DRIVER_NONE;
      else if( starts_with(name, "portaudio") )
         dev->driver = DEV_DRIVER_PORTAUDIO;
      else if( starts_with(name, "pulse") )
         dev->driver = DEV_DRIVER_PULSEAUDIO;
      else if( starts_with(name, "alsa") )
         dev->driver = DEV_DRIVER_ALSA;
      else
         dev->driver = DEV_DRIVER_ALSA;
   }
}

static void open_radio_sound_socket(void)
{
	struct sockaddr_in Addr;
	int samples, port, sndsize = 48000;
	char radio_sound_ip[QUISK_SC_SIZE];
	char radio_sound_mic_ip[QUISK_SC_SIZE];
#ifdef MS_WINDOWS
	WORD wVersionRequested;
	WSADATA wsaData;
#endif

	dc_remove_bw = QuiskGetConfigInt ("dc_remove_bw", 100);
	strncpy(radio_sound_ip, QuiskGetConfigString ("radio_sound_ip", ""), QUISK_SC_SIZE);
	strncpy(radio_sound_mic_ip, QuiskGetConfigString ("radio_sound_mic_ip", ""), QUISK_SC_SIZE);
	if (radio_sound_ip[0] == 0 && radio_sound_mic_ip[0] == 0)
		return;
#ifdef MS_WINDOWS
	wVersionRequested = MAKEWORD(2, 2);
	if (WSAStartup(wVersionRequested, &wsaData) != 0) {
		printf("open_radio_sound_socket: Failure to start WinSock\n");
		return;		// failure to start winsock
	}
#endif
	if (radio_sound_ip[0]) {
		port = QuiskGetConfigInt ("radio_sound_port", 0);
		samples = QuiskGetConfigInt ("radio_sound_nsamples", 360);
		if (samples > 367)
			samples = 367;
		radio_sound_nshorts = samples * 2 + 1;
		radio_sound_socket = socket(PF_INET, SOCK_DGRAM, 0);
		if (radio_sound_socket != INVALID_SOCKET) {
			setsockopt(radio_sound_socket, SOL_SOCKET, SO_SNDBUF, (char *)&sndsize, sizeof(sndsize));
			Addr.sin_family = AF_INET;
			Addr.sin_port = htons(port);
#ifdef MS_WINDOWS
			Addr.sin_addr.S_un.S_addr = inet_addr(radio_sound_ip);
#else
			inet_aton(radio_sound_ip, &Addr.sin_addr);
#endif
			if (connect(radio_sound_socket, (const struct sockaddr *)&Addr, sizeof(Addr)) != 0) {
				close(radio_sound_socket);
				radio_sound_socket = INVALID_SOCKET;
			}
		}
		if (radio_sound_socket == INVALID_SOCKET) {
			printf("open_radio_sound_socket: Failure to open socket\n");
		}
		else {
#if DEBUG_IO
			printf("open_radio_sound_socket: opened socket %s\n", radio_sound_ip);
#endif
		}
	}
	if (radio_sound_mic_ip[0]) {
		port = QuiskGetConfigInt ("radio_sound_mic_port", 0);
		samples = QuiskGetConfigInt ("radio_sound_mic_nsamples", 720);
		if (samples > 734)
			samples = 734;
		radio_sound_mic_nshorts = samples + 1;
		radio_sound_mic_socket = socket(PF_INET, SOCK_DGRAM, 0);
		if (radio_sound_mic_socket != INVALID_SOCKET) {
			setsockopt(radio_sound_mic_socket, SOL_SOCKET, SO_SNDBUF, (char *)&sndsize, sizeof(sndsize));
			Addr.sin_family = AF_INET;
			Addr.sin_port = htons(port);
#ifdef MS_WINDOWS
			Addr.sin_addr.S_un.S_addr = inet_addr(radio_sound_mic_ip);
#else
			inet_aton(radio_sound_mic_ip, &Addr.sin_addr);
#endif
			if (connect(radio_sound_mic_socket, (const struct sockaddr *)&Addr, sizeof(Addr)) != 0) {
				close(radio_sound_mic_socket);
				radio_sound_mic_socket = INVALID_SOCKET;
			}
		}
		if (radio_sound_mic_socket == INVALID_SOCKET) {
			printf("open_radio_sound_mic_socket: Failure to open socket\n");
		}
		else {
#if DEBUG_IO
			printf("open_radio_sound_mic_socket: opened socket %s\n", radio_sound_mic_ip);
#endif
		}
	}
}

void quisk_open_sound(void)	// Called from GUI thread
{
	int i;

	quisk_sound_state.read_error = 0;
	quisk_sound_state.write_error = 0;
	quisk_sound_state.underrun_error = 0;
	quisk_sound_state.mic_read_error = 0;
	quisk_sound_state.interupts = 0;
	quisk_sound_state.rate_min = quisk_sound_state.rate_max = -99;
	quisk_sound_state.chan_min = quisk_sound_state.chan_max = -99;
	quisk_sound_state.msg1[0] = 0;
	quisk_sound_state.err_msg[0] = 0;

	// Set stream names
	strncpy(Capture.name, quisk_sound_state.dev_capt_name, QUISK_SC_SIZE);
	strncpy(Playback.name, quisk_sound_state.dev_play_name, QUISK_SC_SIZE);
	strncpy(MicCapture.name, quisk_sound_state.mic_dev_name, QUISK_SC_SIZE);
	strncpy(MicPlayback.name, quisk_sound_state.name_of_mic_play, QUISK_SC_SIZE);
	strncpy(DigitalInput.name, QuiskGetConfigString ("digital_input_name", ""), QUISK_SC_SIZE);
	strncpy(DigitalOutput.name, QuiskGetConfigString ("digital_output_name", ""), QUISK_SC_SIZE);
	strncpy(RawSamplePlayback.name, QuiskGetConfigString ("sample_playback_name", ""), QUISK_SC_SIZE);
	strncpy(quisk_DigitalRx1Output.name, QuiskGetConfigString ("digital_rx1_name", ""), QUISK_SC_SIZE);
   
	// Set stream descriptions. This is important for "deviceless" drivers like
	// PulseAudio to be able to distinguish the streams from each other.
	strncpy(Capture.stream_description, "I/Q Rx Sample Input", QUISK_SC_SIZE);
	Capture.stream_description[QUISK_SC_SIZE-1] = '\0';
	strncpy(Playback.stream_description, "Radio Sound Output", QUISK_SC_SIZE);
	Playback.stream_description[QUISK_SC_SIZE-1] = '\0';
	strncpy(MicCapture.stream_description, "Microphone Input", QUISK_SC_SIZE);
	MicCapture.stream_description[QUISK_SC_SIZE-1] = '\0';
	strncpy(MicPlayback.stream_description, "I/Q Tx Sample Output", QUISK_SC_SIZE);
	MicPlayback.stream_description[QUISK_SC_SIZE-1] = '\0';
	strncpy(DigitalInput.stream_description, "External Digital Input", QUISK_SC_SIZE);
	strncpy(DigitalOutput.stream_description, "External Digital Output", QUISK_SC_SIZE);
	strncpy(RawSamplePlayback.stream_description, "Raw Digital Output", QUISK_SC_SIZE);
	strncpy(quisk_DigitalRx1Output.stream_description, "Digital Rx1 Output", QUISK_SC_SIZE);
   
	Playback.sample_rate = quisk_sound_state.playback_rate;		// Radio sound play rate
	MicPlayback.sample_rate = quisk_sound_state.mic_playback_rate;
	MicCapture.sample_rate = quisk_sound_state.mic_sample_rate;
	MicCapture.channel_I = quisk_sound_state.mic_channel_I;	// Mic audio is here
	MicCapture.channel_Q = quisk_sound_state.mic_channel_Q;
	// Capture device for digital modes
	DigitalInput.sample_rate = 48000;
	DigitalInput.channel_I = 0;
	DigitalInput.channel_Q = 1;
	// Playback device for digital modes
	digital_output_level = QuiskGetConfigDouble("digital_output_level", 0.7);
	DigitalOutput.sample_rate = quisk_sound_state.playback_rate;	// Radio sound play rate
	DigitalOutput.channel_I = 0;
	DigitalOutput.channel_Q = 1;
	// Playback device for raw samples
	RawSamplePlayback.sample_rate = quisk_sound_state.sample_rate;
	RawSamplePlayback.channel_I = 0;
	RawSamplePlayback.channel_Q = 1;
	// Playback device for digital modes from sub-receivers
	quisk_DigitalRx1Output.sample_rate = 48000;
	quisk_DigitalRx1Output.channel_I = 0;
	quisk_DigitalRx1Output.channel_Q = 1;

	set_num_channels (&Capture);
	set_num_channels (&Playback);
	set_num_channels (&MicCapture);
	set_num_channels (&MicPlayback);
	set_num_channels (&DigitalInput);
	set_num_channels (&DigitalOutput);
	set_num_channels (&RawSamplePlayback);
	set_num_channels (&quisk_DigitalRx1Output);

	Capture.average_square = 0;
	Playback.average_square = 0;
	MicCapture.average_square = 0;
	MicPlayback.average_square = 0;
	DigitalInput.average_square = 0;
	DigitalOutput.average_square = 0;
	RawSamplePlayback.average_square = 0;
	quisk_DigitalRx1Output.average_square = 0;

	//Needed for pulse audio context connection (KM4DSJ)
	Capture.stream_dir_record = 1;
	Playback.stream_dir_record = 0;
	MicCapture.stream_dir_record = 1;
	MicPlayback.stream_dir_record= 0;
	DigitalInput.stream_dir_record = 1;
	DigitalOutput.stream_dir_record = 0;
	RawSamplePlayback.stream_dir_record = 0;
	quisk_DigitalRx1Output.stream_dir_record = 0;

	//For remote IQ server over pulseaudio (KM4DSJ)
	if (quisk_sound_state.IQ_server[0]) {
		strncpy(Capture.server, quisk_sound_state.IQ_server, IP_SIZE);
		strncpy(MicPlayback.server, quisk_sound_state.IQ_server, IP_SIZE);
	}


#ifdef FIX_H101
	Capture.channel_Delay = Capture.channel_Q;	// Obsolete; do not use.
#else
	Capture.channel_Delay = QuiskGetConfigInt ("channel_delay", -1);
#endif
	MicPlayback.channel_Delay = QuiskGetConfigInt ("tx_channel_delay", -1);

	if (pt_sample_read)			// capture from SDR-IQ by Rf-Space or UDP
		Capture.name[0] = 0;	// zero the capture soundcard name
	else						// sound card capture
		Capture.sample_rate = quisk_sound_state.sample_rate;
	// set read size for sound card capture
	i = (int)(quisk_sound_state.data_poll_usec * 1e-6 * Capture.sample_rate + 0.5);
	i = i / 64 * 64;
	if (i > SAMP_BUFFER_SIZE / Capture.num_channels)		// limit to buffer size
		i = SAMP_BUFFER_SIZE / Capture.num_channels;
	Capture.read_frames = i;
	MicCapture.read_frames = 0;		// Use non-blocking read for microphone
	Playback.read_frames = 0;
	MicPlayback.read_frames = 0;
	// set sound card play latency
	Playback.latency_frames = Playback.sample_rate * quisk_sound_state.latency_millisecs / 1000;
	MicPlayback.latency_frames = MicPlayback.sample_rate * quisk_sound_state.latency_millisecs / 1000;
	Capture.latency_frames = 0;
	MicCapture.latency_frames = 0;
	// set capture and playback for digital modes
	DigitalInput.read_frames = 0;		// Use non-blocking read
	DigitalInput.latency_frames = 0;
	DigitalOutput.read_frames = 0;
	DigitalOutput.latency_frames = DigitalOutput.sample_rate * 500 / 1000;	// 500 milliseconds
	quisk_DigitalRx1Output.read_frames = 0;
	quisk_DigitalRx1Output.latency_frames = quisk_DigitalRx1Output.sample_rate * 500 / 1000;	// 500 milliseconds
	// set capture and playback for raw samples
	RawSamplePlayback.read_frames = 0;
	RawSamplePlayback.latency_frames = RawSamplePlayback.sample_rate * 500 / 1000;	// 500 milliseconds
	open_radio_sound_socket();
#if DEBUG_IO
	printf("Sample buffer size %d, latency msec %d\n", SAMP_BUFFER_SIZE, quisk_sound_state.latency_millisecs);
#endif
}

void quisk_start_sound(void)	// Called from sound thread
{
	if (pt_sample_start)
		(*pt_sample_start)();
   
	// Decide which drivers start which devices.
	decide_drivers(CaptureDevices);
	decide_drivers(PlaybackDevices);
   
	// Let the drivers see the devices and start them up if appropriate
	quisk_start_sound_portaudio(CaptureDevices, PlaybackDevices);
	quisk_start_sound_pulseaudio(CaptureDevices, PlaybackDevices);
	quisk_start_sound_alsa(CaptureDevices, PlaybackDevices);
   
	if (pt_sample_read) {	// Capture from SDR-IQ or UDP
		quisk_sound_state.rate_min = Playback.rate_min;
		quisk_sound_state.rate_max = Playback.rate_max;
		quisk_sound_state.chan_min = Playback.chan_min;
		quisk_sound_state.chan_max = Playback.chan_max;
	}
	else {					// Capture from sound card
		quisk_sound_state.rate_min = Capture.rate_min;
		quisk_sound_state.rate_max = Capture.rate_max;
		quisk_sound_state.chan_min = Capture.chan_min;
		quisk_sound_state.chan_max = Capture.chan_max;
	}
	QuiskDeltaMsec(0);	// Set timer to zero
	QuiskDeltaMsec(1);
}

PyObject * quisk_set_ampl_phase(PyObject * self, PyObject * args)	// Called from GUI thread
{  /*	Set the sound card amplitude and phase corrections.  See
	S.W. Ellingson, Correcting I-Q Imbalance in Direct Conversion Receivers, February 10, 2003 */
	struct sound_dev * dev;
	double ampl, phase;
	int is_tx;		// Is this for Tx?  Otherwise Rx.

	if (!PyArg_ParseTuple (args, "ddi", &ampl, &phase, &is_tx))
		return NULL;
	if (is_tx)
		dev = &MicPlayback;
	else
		dev = &Capture;
	if (ampl == 0.0 && phase == 0.0) {
		dev->doAmplPhase = 0;
	}
	else {
		dev->doAmplPhase = 1;
		ampl = ampl + 1.0;			// Change factor 0.01 to 1.01
		phase = (phase / 360.0) * 2.0 * M_PI;	// convert to radians
		dev->AmPhAAAA = 1.0 / ampl;
		dev->AmPhCCCC = - dev->AmPhAAAA * tan(phase);
		dev->AmPhDDDD = 1.0 / cos(phase);
	}
	Py_INCREF (Py_None);
	return Py_None;
}

PyObject * quisk_capt_channels(PyObject * self, PyObject * args)	// Called from GUI thread
{
	if (!PyArg_ParseTuple (args, "ii", &Capture.channel_I, &Capture.channel_Q))
		return NULL;
	Py_INCREF (Py_None);
	return Py_None;
}

PyObject * quisk_play_channels(PyObject * self, PyObject * args)	// Called from GUI thread
{
	if (!PyArg_ParseTuple (args, "ii", &Playback.channel_I, &Playback.channel_Q))
		return NULL;
	Py_INCREF (Py_None);
	return Py_None;
}

PyObject * quisk_micplay_channels(PyObject * self, PyObject * args)	// Called from GUI thread
{
	if (!PyArg_ParseTuple (args, "ii", &MicPlayback.channel_I, &MicPlayback.channel_Q))
		return NULL;
	Py_INCREF (Py_None);
	return Py_None;
}

PyObject * quisk_set_sparams(PyObject * self, PyObject * args, PyObject * keywds)
{  /* Call with keyword arguments ONLY; change local parameters */
	static char * kwlist[] = {"dc_remove_bw", "digital_output_level", NULL} ;

	if (!PyArg_ParseTupleAndKeywords (args, keywds, "|id", kwlist, &dc_remove_bw, &digital_output_level))
		return NULL;
	Py_INCREF (Py_None);
	return Py_None;
}

void quisk_udp_mic_error(char * msg)
{
	MicCapture.dev_error++;
#if DEBUG_IO
	printf("%s\n", msg);
#endif
}

static void AddCard(struct sound_dev * dev, PyObject * pylist)
{
	PyObject * v;

	if (dev->name[0]) {
		v = Py_BuildValue("(NNiiid)",
			PyUnicode_DecodeUTF8(dev->stream_description, strlen(dev->stream_description), "replace"),
			PyUnicode_DecodeUTF8(dev->name, strlen(dev->name), "replace"),
			dev->sample_rate, dev->dev_latency, dev->dev_error + dev->dev_underrun, dev->average_square);
		PyList_Append(pylist, v);
	}
}

PyObject * quisk_sound_errors(PyObject * self, PyObject * args)
{  // return a list of strings with card names and error counts
	PyObject * pylist;

	if (!PyArg_ParseTuple (args, ""))
		return NULL;
	pylist = PyList_New(0);
	AddCard(&Capture,	pylist);
	AddCard(&MicCapture,	pylist);
	AddCard(&DigitalInput,	pylist);
	AddCard(&Playback,	pylist);
	AddCard(&MicPlayback,	pylist);
	AddCard(&DigitalOutput,	pylist);
	AddCard(&RawSamplePlayback, pylist);
	AddCard(&quisk_DigitalRx1Output, pylist);
	return pylist;
}

PyObject * quisk_set_file_name(PyObject * self, PyObject * args, PyObject * keywds)  // called from GUI
{  // Set the names and enable state of the recording and playback files.
	int which = -1;
	const char * name = NULL;
	int enable = -1;
	int play_button = -1;
	int record_button = -1;
	static char * kwlist[] = {"which", "name", "enable", "play_button", "record_button", NULL} ;

	if (!PyArg_ParseTupleAndKeywords (args, keywds, "|isiii", kwlist, &which, &name, &enable, &play_button, &record_button))
		return NULL;
	switch (which) {
	case 0:		// record audio file
		if (name)
			strncpy(file_rec_audio.file_name, name, QUISK_PATH_SIZE);
		if (enable != -1)
			file_rec_audio.enable = enable;
		break;
	case 1:		// record sample file
		if (name)
			strncpy(file_rec_samples.file_name, name, QUISK_PATH_SIZE);
		if (enable != -1)
			file_rec_samples.enable = enable;
		break;
	case 2:		// record mic file
		if (name)
			strncpy(file_rec_mic.file_name, name, QUISK_PATH_SIZE);
		if (enable != -1)
			file_rec_mic.enable = enable;
		break;
	case 10:	// play audio file
		break;
	case 11:	// play samples file
		break;
	case 12:	// play CQ message file
		break;
	}
	if (record_button != -1)
		file_record_button = record_button;
	if (file_rec_audio.enable && file_record_button){	// Open and Close Rx audio file
		if ( ! file_rec_audio.fp)
			record_audio(&file_rec_audio, NULL, -1);	// Open file
	}
	else if (file_rec_audio.fp) {
		record_audio(&file_rec_audio, NULL, -2);		// Close file
	}
	if (file_rec_mic.enable && file_record_button){		// Open and Close microphone audio file
		if ( ! file_rec_mic.fp)
			record_audio(&file_rec_mic, NULL, -1);
	}
	else if (file_rec_mic.fp) {
		record_audio(&file_rec_mic, NULL, -2);
	}
	if (file_rec_samples.enable && file_record_button){ // Open and Close I/Q samples file
		if ( ! file_rec_samples.fp)
			record_samples(&file_rec_samples, NULL, -1);
	}
	else if (file_rec_samples.fp) {
		record_samples(&file_rec_samples, NULL, -2);
	}
	Py_INCREF (Py_None);
	return Py_None;
}
