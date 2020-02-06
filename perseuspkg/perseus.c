#include <Python.h>
#include <stdio.h> 
#include <string.h> 
#include <fcntl.h> 
#include <sys/stat.h> 
#include <sys/types.h> 
#include <unistd.h> 
#include <complex.h>
#include <perseus-sdr.h>

#define IMPORT_QUISK_API
#include "quisk.h"
#include "filter.h"

// This module was written by Andrea Montefusco IW0HDV.

typedef union {
	struct {
		int32_t	i;
		int32_t	q;
		} __attribute__((__packed__)) iq;
	struct {
		uint8_t		i1;
		uint8_t		i2;
		uint8_t		i3;
		uint8_t		i4;
		uint8_t		q1;
		uint8_t		q2;
		uint8_t		q3;
		uint8_t		q4;
		} __attribute__((__packed__)) ;
} iq_sample;



// This module uses the Python interface to import symbols from the parent _quisk
// extension module.  It must be linked with import_quisk_api.c.  See the documentation
// at the start of import_quisk_api.c.

#define DEBUG		1

static int shutdown_sample_device;
static 	perseus_descr *descr;
static int sr = 48000;
static float freq = 7050000.0;

static void quisk_stop_samples(void);

static const char *fname = "/tmp/quiskperseus";
static int rfd = 0;
static int wfd = 0;


// Called in a loop to read samples; called from the sound thread.
static int quisk_read_samples(complex double * cSamples)
{
	//fprintf (stderr, "r"); fflush(stderr);

	int n = read(rfd, cSamples, sizeof(complex double)*SAMP_BUFFER_SIZE); 
	//fprintf(stderr, "%d ", n);
	if (n >= 0)
		return n/sizeof(complex double);	// return number of samples
	else
		return 0;
}

// Called in a loop to write samples; called from the sound thread.
static int quisk_write_samples(complex double * cSamples, int nSamples)
{
	return 0;
}


//
// callback that writes in the output stream I-Q values as 32 bits
// floating point in -1.0 ... +1.0 range
//
static int user_data_callback_c_f(void *buf, int buf_size, void *extra)
{
	// The buffer received contains 24-bit IQ samples (6 bytes per sample)
	// Here as a demonstration we save the received IQ samples as 32 bit 
	// (msb aligned) integer IQ samples.

	// At the maximum sample rate (2 MS/s) the hard disk should be capable
	// of writing data at a rate of at least 16 MB/s (almost 1 GB/min!)
	//fprintf (stderr, "."); fflush(stderr);

	uint8_t	*samplebuf 	= (uint8_t*)buf;
	int nSamples 		= buf_size/6;
	int k;
	iq_sample s;

	// the 24 bit data is scaled to a 32bit value (so that the machine's
	// natural signed arithmetic will work), and then use a simple
	// ratio of the result with the maximum possible value
	// which is INT_MAX less 256 because of the vacant lower 8 bits
	for (k=0;k<nSamples;k++) {
		s.i1 = s.q1 = 0;
		s.i2 = *samplebuf++;
		s.i3 = *samplebuf++;
		s.i4 = *samplebuf++;
		s.q2 = *samplebuf++;
		s.q3 = *samplebuf++;
		s.q4 = *samplebuf++;

		float i, q;
		// convert to float in [-1.0 - +1.0] range
		//iq_f[0] = (float)(s.iq.i) / (INT_MAX - 256);
		//iq_f[1] = (float)(s.iq.q) / (INT_MAX - 256);
		i = (float)(s.iq.i);
		q = (float)(s.iq.q);
		
		complex double x = (double)i + (double)q * _Complex_I;

		if (wfd > 0) {
			int n = write(wfd, &x, sizeof(complex double));
			//fprintf(stderr, " *%d* ", n);
			if (n<0 && ! -EAGAIN ) fprintf(stderr, "Can't write output file: %s, descriptor: %d\n", strerror(errno), wfd);
		}
	}
    return 0;
}



// Start sample capture; called from the sound thread.
static void quisk_start_samples(void)
{
	int nb = 6;
	int bs = 1024;
	
	fprintf (stderr, "SSSSSSSSSSSSSSSSSSSSSSSSSSSS\n"); fflush(stderr);

	shutdown_sample_device = 0;

	int rc = mkfifo(fname, 0666); 
	
	if ((rc == -1) && (errno != EEXIST)) {
		perror("Error creating the named pipe");
	}
	fprintf (stderr, "SSSSSSSSSSSSSSSSSSSSSSSSSSSS\n"); fflush(stderr);

	rfd = open(fname, O_RDONLY|O_NONBLOCK); 
	if (rfd < 0) fprintf(stderr, "Can't open read FIFO (%s)\n", strerror(errno));
	else  fprintf(stderr, "read FIFO (%d)\n", rfd);

	wfd = open(fname, O_WRONLY|O_NONBLOCK);
	if (wfd < 0) fprintf(stderr, "Can't open write FIFO (%s)\n", strerror(errno));
	else  fprintf(stderr, "write FIFO (%d)\n", wfd);

	if (perseus_start_async_input(descr, nb*bs, user_data_callback_c_f, 0)<0) {
		fprintf(stderr, "start async input error: %s\n", perseus_errorstr());
	} else
		fprintf(stderr, "start async\n");
}

// Stop sample capture; called from the sound thread.
static void quisk_stop_samples(void)
{
	shutdown_sample_device = 1;
	// We stop the acquisition...
	fprintf(stderr, "Stopping async data acquisition...\n");
	perseus_stop_async_input(descr);
	close(rfd);
	close(wfd);
	unlink(fname);
}


// Called to close the sample source; called from the GUI thread.
static PyObject * close_device(PyObject * self, PyObject * args)
{
	int sample_device;

	if (!PyArg_ParseTuple (args, "i", &sample_device))
		return NULL;

	if (descr) {
		// We stop the acquisition...
		fprintf(stderr, "Quitting...\n");
		perseus_close(descr);
		descr=NULL;
		perseus_exit();
	}

	Py_INCREF (Py_None);
	return Py_None;
}

// Called to open the Perseus SDR device; called from the GUI thread.
static PyObject * open_device(PyObject * self, PyObject * args)
{
	char buf128[128] = "Capture Microtelecom Perseus HF receiver";
	eeprom_prodid prodid;

	fprintf (stderr, "OOOOOOOOOOOOOOOOOOOOO\n"); fflush(stderr);
	

	// Check how many Perseus receivers are connected to the system
	int num_perseus = perseus_init();
	fprintf(stderr, "%d Perseus receivers found\n",num_perseus);

	if (num_perseus==0) {
		sprintf(buf128, "No Perseus receivers detected\n");
		perseus_exit();
		goto main_cleanup;
	}

	// Open the first one...
	if ((descr=perseus_open(0))==NULL) {
		sprintf(buf128, "error: %s\n", perseus_errorstr());
		goto main_cleanup;
	}

	// Download the standard firmware to the unit
	fprintf(stderr, "Downloading firmware...\n");
	if (perseus_firmware_download(descr,NULL)<0) {
		sprintf(buf128, "firmware download error: %s", perseus_errorstr());
		goto main_cleanup;
	}
	// Dump some information about the receiver (S/N and HW rev)
	if (perseus_is_preserie(descr, 0) ==  PERSEUS_SNNOTAVAILABLE)
		fprintf(stderr, "The device is a preserie unit");
	else
		if (perseus_get_product_id(descr,&prodid)<0) 
			fprintf(stderr, "get product id error: %s", perseus_errorstr());
		else
			fprintf(stderr, "Receiver S/N: %05d-%02hX%02hX-%02hX%02hX-%02hX%02hX - HW Release:%hd.%hd\n",
					(uint16_t) prodid.sn, 
					(uint16_t) prodid.signature[5],
					(uint16_t) prodid.signature[4],
					(uint16_t) prodid.signature[3],
					(uint16_t) prodid.signature[2],
					(uint16_t) prodid.signature[1],
					(uint16_t) prodid.signature[0],
					(uint16_t) prodid.hwrel,
					(uint16_t) prodid.hwver);

    // Printing all sampling rates available .....
    {
        int buf[BUFSIZ];

        if (perseus_get_sampling_rates (descr, buf, sizeof(buf)/sizeof(buf[0])) < 0) {
			fprintf(stderr, "get sampling rates error: %s\n", perseus_errorstr());
			goto main_cleanup;
        } else {
            int i = 0;
            while (buf[i]) {
                fprintf(stderr, "#%d: sample rate: %d\n", i, buf[i]);
                i++;
            }
        }
    }

	// Configure the receiver for 2 MS/s operations
	fprintf(stderr, "Configuring FPGA...\n");
	if (perseus_set_sampling_rate(descr, sr) < 0) {  // specify the sampling rate value in Samples/second
	//if (perseus_set_sampling_rate_n(descr, 0)<0)        // specify the sampling rate value as ordinal in the vector
		fprintf(stderr, "fpga configuration error: %s\n", perseus_errorstr());
		goto main_cleanup;
	}
	
	// Disable preselection filters (WB_MODE On)
	perseus_set_ddc_center_freq(descr, freq, 0);
	sleep(1);
	// Re-enable preselection filters (WB_MODE Off)
	perseus_set_ddc_center_freq(descr, freq, 1);
	
	quisk_sample_source4(&quisk_start_samples, &quisk_stop_samples, &quisk_read_samples, &quisk_write_samples);
	
	fprintf (stderr, "CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC\n"); fflush(stderr);
	goto exit_success;



	main_cleanup:


	//if (!PyArg_ParseTuple (args, "sii", &name, &sample_device, &poll))
	//	return NULL;
	//sdev = SoapySDRDevice_makeStrArgs(name);
	//if(sdev) {
	//	snprintf(buf128, 128, "Capture from %s", name);
	//	if (sample_device) {
	//		shutdown_sample_device = 0;
	//		soapy_sample_device = sdev;
	//		data_poll_usec = poll;
	//		quisk_sample_source4(&quisk_start_samples, &quisk_stop_samples, &quisk_read_samples, &quisk_write_samples);
	//		numTxChannels = SoapySDRDevice_getNumChannels(sdev, SOAPY_SDR_TX);
	//		if (sample_device == 3)		// disable transmit
	//			numTxChannels = 0;
	//	}
	//	else {
	//		soapy_config_device = sdev;
	//	}
	//}
	//else {
	//	snprintf(buf128, 128, "SoapySDRDevice_make fail: %s", SoapySDRDevice_lastError());
	//}
	
	exit_success:
	
	return PyString_FromString(buf128);
	
	
}

static PyObject * set_frequency(PyObject * self, PyObject * args)	// Called from GUI thread
{  // Parameter name can end in "_rx" or "_tx" to specify direction.
	
	float param;
	
	if (!PyArg_ParseTuple (args, "f", &param))
		return NULL;
	if (DEBUG)
		printf ("Set %lf\n", param);
	freq= param;
	if (descr) perseus_set_ddc_center_freq(descr, freq, 0);

	Py_INCREF (Py_None);
	return Py_None;
}


static PyObject * set_sampling_rate(PyObject * self, PyObject * args)	// Called from GUI thread
{  // Parameter name can end in "_rx" or "_tx" to specify direction.
	
	int param;
	
	if (!PyArg_ParseTuple (args, "i", &param))
		return NULL;
	if (DEBUG)
		printf ("Set sampling rate %d\n", param);
	if (param < 48000) sr= param*1000;
	else sr = param;
	if (descr)
		// specify the sampling rate value in Samples/secon
		if (perseus_set_sampling_rate(descr, sr) < 0) {
			fprintf(stderr, "fpga configuration error: %s\n", perseus_errorstr());
		}

	Py_INCREF (Py_None);
	return Py_None;
}


static PyObject * get_parameter(PyObject * self, PyObject * args)	// Called from GUI thread
{ // Return a SoapySDR parameter.
  // Parameter name can end in "_rx" or "_tx" to specify direction.
	int sample_device, direction, length = 0;
	char * name;
	char ** names;
	size_t i, len_list;
	//bool is_true;
	double value;
	PyObject * pylist, * pyobj, * pylst2;
	//SoapySDRDevice * sdev;
	//SoapySDRRange range;
	//SoapySDRRange * ranges;

	if (!PyArg_ParseTuple (args, "si", &name, &sample_device))
		return NULL;
	//if (sample_device)
	//	descr = perseus_sample_device;
	//else
	//	descr = perseus_config_device;
	//get_direc_len(name, &direction, &length);
	if ( ! descr) {
		;
	}
	else if ( ! strcmp(name, "perseus_getSampleRateRange")) {
		pylist = PyList_New(0);
		//ranges = SoapySDRDevice_getSampleRateRange(sdev, direction, 0, &len_list);
		// 48000, 96000, 192000
		int ranges [] = {48000, 96000, 192000} ;
		for (i = 0; i < 3; i++) {
			PyObject * pyobj;

			pyobj = PyFloat_FromDouble(ranges[i]);
			PyList_Append(pylist, pyobj);
			Py_DECREF(pyobj);
			
		}
		return pylist;
	}
	else {
		printf("Perseus get_parameter() for unknown name %s\n", name);
	}
	Py_INCREF (Py_None);
	return Py_None;
}



static PyObject * set_parameter(PyObject * self, PyObject * args)	// Called from GUI thread
{  // Parameter name can end in "_rx" or "_tx" to specify direction.
	const char * param;	// name of the parameter
	const char * name2;	// string data or sub-parameter name if any
	double datum;		// floating point value if any
	char msg200[200] = {0};

	if (!PyArg_ParseTuple (args, "ssd", &param, &name2, &datum))
		return NULL;
	
	fprintf (stderr, "Set %s - %s - %lf\n", param, name2, datum);

	if (descr) {
		if ( ! strcmp(param, "soapy_setFrequency")) {
			if (descr) {
				freq = datum;
				if (perseus_set_ddc_center_freq(descr, freq, 0) < 0 ) {
					fprintf(stderr, "frequency configuration error: %s\n", perseus_errorstr());
					snprintf(msg200, 200, "%s fail: %s\n", param, perseus_errorstr());
				} else
					fprintf(stderr, "fpga configuration frequency: %f\n", freq);

			}
		}
		else if ( ! strcmp(param, "soapy_setSampleRate")) {
			if (descr) {
				sr = datum;
				// specify the sampling rate value in Samples/secon
				if (perseus_set_sampling_rate(descr, sr) < 0) {
					fprintf(stderr, "fpga configuration error: %s\n", perseus_errorstr());
					snprintf(msg200, 200, "Perseus %s fail: %s\n", param, perseus_errorstr());
				} else
					fprintf(stderr, "fpga configuration sample rate: %d\n", sr);
			}
		}
		else {
			snprintf(msg200, 200, "Perseus set_parameter() for unknown name %s\n", param);
		}
	}
	if (msg200[0])
		return PyString_FromString(msg200);
	Py_INCREF (Py_None);
	return Py_None;
}



// Functions callable from Python are listed here:
static PyMethodDef QuiskMethods[] = {
	{"open_device", open_device, METH_VARARGS, "Open the hardware."},
	{"close_device", close_device, METH_VARARGS, "Close the hardware"},
//	{"get_device_list", get_device_list, METH_VARARGS, "Return a list of SoapySDR devices"},
	{"set_frequency", set_frequency, METH_VARARGS, "set frequency"},
	{"set_sampling_rate", set_sampling_rate, METH_VARARGS, "set sampling rate"},
	{"get_parameter", get_parameter, METH_VARARGS, "Get a PerseusSDR parameter"},
	{"set_parameter", set_parameter, METH_VARARGS, "Set a PerseusSDR parameter"},
	{NULL, NULL, 0, NULL}		/* Sentinel */
};

#if PY_MAJOR_VERSION < 3
// Python 2.7:
// Initialization, and registration of public symbol "initperseus":
PyMODINIT_FUNC initperseus (void)
{
	if (Py_InitModule ("perseus", QuiskMethods) == NULL) {
		printf("Py_InitModule failed!\n");
		return;
	}
	// Import pointers to functions and variables from module _quisk
	if (import_quisk_api()) {
		printf("Failure to import pointers from _quisk\n");
		return;		//Error
	}
}

// Python 3:
#else
static struct PyModuleDef perseusmodule = {
	PyModuleDef_HEAD_INIT,
	"perseus",
	NULL,
	-1,
	QuiskMethods
} ;

PyMODINIT_FUNC PyInit_perseus(void)
{
	PyObject * m;

	m = PyModule_Create(&perseusmodule);
	if (m == NULL)
		return NULL;

	// Import pointers to functions and variables from module _quisk
	if (import_quisk_api()) {
		printf("Failure to import pointers from _quisk\n");
		return m;		//Error
	}
	return m;
}
#endif
