# This is a sample hardware file for UDP control using the Hermes-Metis protocol.  Use this for
# the HermesLite project.  It can also be used for the HPSDR, but since I don't have one, I
# can't test it.

from __future__ import print_function
from __future__ import absolute_import
from __future__ import division

import socket, traceback, time, math, os
import wx
import _quisk as QS
import quisk_utils

from quisk_hardware_model import Hardware as BaseHardware

DEBUG = 0

class Hardware(BaseHardware):
  var_rates = ['48', '96', '192', '384']
  def __init__(self, app, conf):
    BaseHardware.__init__(self, app, conf)
    self.var_index = 0
    self.hermes_mac = bytearray(6)
    self.hermes_ip = ""
    self.hermes_code_version = -1
    self.hermes_board_id = -1
    self.hermes_temperature = 0.0
    self.hermes_fwd_power = 0.0
    self.hermes_rev_power = 0.0
    self.hermes_pa_current = 0.0
    self.eeprom_valid = 0
    self.alex_hpf_f1 = 0
    self.alex_hpf_f2 = 0
    self.alex_lpf_f1 = 0
    self.alex_lpf_f2 = 0
    self.mode = None
    self.band = None
    self.vfo_frequency = 0
    self.tx_frequency = 0
    self.vna_count = 0
    self.vna_started = False
    self.repeater_freq = None		# original repeater output frequency
    try:
      self.repeater_delay = conf.repeater_delay		# delay for changing repeater frequency in seconds
    except:
      self.repeater_delay = 0.25
    self.repeater_time0 = 0			# time of repeater change in frequency
    # Create the proper broadcast addresses for socket_discover
    if conf.udp_rx_ip:		# Known IP address of hardware
      self.broadcast_addrs = [conf.udp_rx_ip]
    else:
      self.broadcast_addrs = []
      for interf in QS.ip_interfaces():
        broadc = interf[3]	# broadcast address
        if broadc and broadc[0:4] != '127.':
          self.broadcast_addrs.append(broadc)
      self.broadcast_addrs.append('255.255.255.255')
    if DEBUG: print ('broadcast_addrs', self.broadcast_addrs)
    # This is the control data to send to the Hermes using the Metis protocol
    # Duplex must be on or else the first Rx frequency is locked to the Tx frequency
    self.pc2hermes = bytearray(17 * 4)		# Control bytes not including C0.  Python initializes this to zero.
    self.pc2hermeslitewritequeue = bytearray(4 * 5)
    self.pc2hermes[3] = 0x04	# C0 index == 0, C4[5:3]: number of receivers 0b000 -> one receiver; C4[2] duplex on
    self.pc2hermes[4 * 9] = 63	# C0 index == 0b1001, C1[7:0] Tx level
    for c0 in range(1, 9):		# Set all frequencies to 7012352, 0x006B0000
      self.SetControlByte(c0, 2, 0x6B)
    value = conf.keyupDelay
    if value > 1023:
      value = 1023
    self.SetControlByte(0x10, 2, value & 0x3)		# cw_hang_time
    self.SetControlByte(0x10, 1, (value >> 2) & 0xFF)	# cw_hang_time
    self.SetLowPwrEnable(conf.hermes_lowpwr_tr_enable)
    self.EnablePowerAmp(conf.hermes_power_amp)
    self.ChangeTxLNA(conf.hermes_TxLNA_dB)
    self.MakePowerCalibration()
  def pre_open(self):
    # This socket is used for the Metis Discover protocol
    self.discover_request = b"\xEF\xFE\x02" + b"\x00" * 60
    self.socket_discover = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    self.socket_discover.setblocking(0)
    self.socket_discover.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    found = False
    st = "No capture device found."
    port = self.conf.rx_udp_port
    for i in range(5):
      if found:
        break
      if DEBUG: print ('Send discover')
      try:
        for broadcast_addr in self.broadcast_addrs:
          self.socket_discover.sendto(self.discover_request, (broadcast_addr, port))
          if DEBUG: print ('discover_request', (broadcast_addr, port))
          time.sleep(0.01)
      except:
        if DEBUG > 1: traceback.print_exc()
      for j in range(5):
        try:
          data, addr = self.socket_discover.recvfrom(1500)
        except:
          if DEBUG > 1: traceback.print_exc()
          time.sleep(0.02)
          continue
        else:
          if DEBUG: print('recvfrom', addr, 'length', len(data), "type", type(data))
        data = bytearray(data)
        if len(data) > 32 and data[0] == 0xEF and data[1] == 0xFE:
          if DEBUG: print('data', data)
          ver = self.conf.hermes_code_version
          bid = self.conf.hermes_board_id
          if ver >= 0 and data[9] != ver:
            pass
          elif bid >= 0 and data[10] != bid:
            pass
          else:
            st = 'Capture from Hermes device: Mac %2x:%2x:%2x:%2x:%2x:%2x, Code version %d, ID %d' % tuple(data[3:11])
            self.hermes_mac = data[3:9]
            self.hermes_ip = addr[0]
            self.hermes_code_version = data[9]
            self.hermes_board_id = data[10]
            QS.set_hermes_id(data[9], data[10])
            if data[0x16] >> 6 == 0:
              QS.set_params(bandscopeScale = 2048)
            if DEBUG: print (st)
            adr = self.conf.rx_udp_ip
            found = True
            if adr and adr != addr[0]:		# Change the IP address
              if DEBUG: print("Change IP address from %s to %s" % (addr[0], adr))
              ip = adr.split('.')
              ip = list(map(int, ip))
              cmd = bytearray(73)
              cmd[0] = 0xEF
              cmd[1] = 0xFE
              cmd[2] = 0x03
              cmd[3] = data[3]
              cmd[4] = data[4]
              cmd[5] = data[5]
              cmd[6] = data[6]
              cmd[7] = data[7]
              cmd[8] = data[8]
              cmd[9] = ip[0]
              cmd[10] = ip[1]
              cmd[11] = ip[2]
              cmd[12] = ip[3]
              for broadcast_addr in self.broadcast_addrs:
                self.socket_discover.sendto(cmd, (broadcast_addr, port))
                time.sleep(0.01)
              # Note: There is no response, contrary to the documentation
              self.hermes_ip = adr
              if False:
                try:
                  data, addr = self.socket_discover.recvfrom(1500)
                except:
                  if DEBUG: traceback.print_exc()
                else:
                  print(repr(data), addr)
                  ##self.hermes_ip = adr
                time.sleep(1.0)
            st += ', IP %s' % self.hermes_ip
            break
    if not found and self.conf.udp_rx_ip:
      self.hermes_ip = self.conf.udp_rx_ip
      code = 62
      bid = 6
      self.hermes_code_version = code
      self.hermes_board_id = bid
      QS.set_hermes_id(code, bid)
      st = 'Capture from Hermes device at specified IP %s' % self.hermes_ip
      found = True
    if found:
      # Open a socket for communication with the hardware
      msg = QS.open_rx_udp(self.hermes_ip, port)
      if msg[0:8] != "Capture ":
        st = msg		# Error
    self.socket_discover.close()
    self.config_text = st
    self.ChangeLNA(2)	# Initialize the LNA using the correct LNA code from the FPGA code version
  def open(self):
    return self.config_text
  def GetValue(self, name):	# return values stored in the hardware
    if name == 'Hware_Hl2_EepromIP':
      addr1 = self.ReadEEPROM(0x08)
      addr2 = self.ReadEEPROM(0x09)
      addr3 = self.ReadEEPROM(0x0A)
      addr4 = self.ReadEEPROM(0x0B)
      if addr1 < 0 or addr2 < 0 or addr3 < 0 or addr4 < 0:
        return "Read failed"
      else:
        return "%d.%d.%d.%d" % (addr1, addr2, addr3, addr4)
    elif name == 'Hware_Hl2_EepromIPUse':
      use = self.ReadEEPROM(0x06)
      if use < 0:
        return "Read failed"
      if not use & 0b10000000:
        return 'Ignore'
      elif use & 0b100000:
        return 'Use DHCP first'
      else:
        return 'Set address'
    elif name == 'Hware_Hl2_EepromMAC':
      addr1 = self.ReadEEPROM(0x0C)
      addr2 = self.ReadEEPROM(0x0D)
      if addr1 < 0 or addr2 < 0:
        return "Read failed"
      else:
        return "0x%X  0x%X" % (addr1, addr2)
    elif name == 'Hware_Hl2_EepromMACUse':
      use = self.ReadEEPROM(0x06)
      if use < 0:
        return "Read failed"
      if use & 0b1000000:
        return 'Set address'
      else:
        return 'Ignore'
    return "Name failed"
  def SetValue(self, ctrl):
    name = ctrl.quisk_data_name
    value = ctrl.GetValue()
    if name == 'Hware_Hl2_EepromIP':
      try:
        addr1, addr2, addr3, addr4 = value.split('.')
        addr1 = int(addr1)
        addr2 = int(addr2)
        addr3 = int(addr3)
        addr4 = int(addr4)
      except:
        pass
      else:
        self.WriteEEPROM(0x08, addr1)
        self.WriteEEPROM(0x09, addr2)
        self.WriteEEPROM(0x0A, addr3)
        self.WriteEEPROM(0x0B, addr4)
    elif name == 'Hware_Hl2_EepromIPUse':
      use = self.ReadEEPROM(0x06)
      if use >= 0:
        self.eeprom_valid = use
      if value == 'Ignore':
        self.eeprom_valid &= ~0b0010000000
      elif value == 'Use DHCP first':
        self.eeprom_valid |=  0b0010100000
      elif value == 'Set address':
        self.eeprom_valid |=  0b0010000000
        self.eeprom_valid &= ~0b0000100000
      self.WriteEEPROM(0x06, self.eeprom_valid)
    elif name == 'Hware_Hl2_EepromMAC':
      try:
        addr1, addr2 = value.split()
        addr1 = int(addr1, base=0)
        addr2 = int(addr2, base=0)
      except:
        pass
      else:
        self.WriteEEPROM(0x0C, addr1)
        self.WriteEEPROM(0x0D, addr2)
    elif name == 'Hware_Hl2_EepromMACUse':
      use = self.ReadEEPROM(0x06)
      if use >= 0:
        self.eeprom_valid = use
      if value == 'Ignore':
        self.eeprom_valid &= ~0b0001000000
      elif value == 'Set address':
        self.eeprom_valid |=  0b0001000000
      self.WriteEEPROM(0x06, self.eeprom_valid)
  def GetControlByte(self, C0_index, byte_index):
    # Get the control byte at C0 index and byte index.  The bytes are C0, C1, C2, C3, C4.
    # The C0 index is 0 to 16 inclusive.  The byte index is 1 to 4.  The byte index of C2 is 2.
    return self.pc2hermes[C0_index * 4 + byte_index - 1]
  def SetControlByte(self, C0_index, byte_index, value):		# Set the control byte as above.
    self.pc2hermes[C0_index * 4 + byte_index - 1] = value
    QS.pc_to_hermes(self.pc2hermes)
    if DEBUG: print ("SetControlByte C0_index %d byte_index %d to 0x%X" % (C0_index, byte_index, value))
  def ChangeFrequency(self, tx_freq, vfo_freq, source='', band='', event=None):
    if tx_freq and tx_freq > 0:
      if source == 'BtnBand' or abs(tx_freq - self.tx_frequency) > 1000000:
        self.ChangeAlexFilters(tx_freq=tx_freq)
      self.tx_frequency = tx_freq
      tx = int(tx_freq - self.transverter_offset)
      self.pc2hermes[ 4] = tx >> 24 & 0xff		# C0 index == 1, C1, C2, C3, C4: Tx freq, MSB in C1
      self.pc2hermes[ 5] = tx >> 16 & 0xff
      self.pc2hermes[ 6] = tx >>  8 & 0xff
      self.pc2hermes[ 7] = tx       & 0xff
    if self.vfo_frequency != vfo_freq:
      self.vfo_frequency = vfo_freq
      vfo = int(vfo_freq - self.transverter_offset)
      self.pc2hermes[ 8] = vfo >> 24 & 0xff		# C0 index == 2, C1, C2, C3, C4: Rx freq, MSB in C1
      self.pc2hermes[ 9] = vfo >> 16 & 0xff
      self.pc2hermes[10] = vfo >>  8 & 0xff
      self.pc2hermes[11] = vfo       & 0xff
    if DEBUG > 1: print("Change freq Tx", tx_freq, "Rx", vfo_freq)
    QS.pc_to_hermes(self.pc2hermes)
    return tx_freq, vfo_freq
  def ChangeAlexFilters(self, tx_freq=None, edit=False):
    if tx_freq is None:
      tx_freq = self.tx_frequency
    fmin = tx_freq
    fmax = tx_freq
    for pane in self.application.multi_rx_screen.receiver_list:
      freq = pane.VFO + pane.txFreq
      if freq < fmin:
        fmin = freq
      if freq > fmax:
        fmax = freq
    if DEBUG: print ('fmin', fmin, 'fmax', fmax)
    if edit or not (self.alex_hpf_f1 <= fmin < self.alex_hpf_f2):    # Within same HP filter?
      self.alex_hpf_f1, self.alex_hpf_f2, rx, tx = self.FreqAlexFilters(fmin, self.conf.AlexHPF, self.conf.AlexHPF_TxEn)
      if DEBUG: print ("Change HP filter fmin, f1, f2", fmin, self.alex_hpf_f1, self.alex_hpf_f2, "rx, tx", rx, tx)
      QS.set_alex_hpf(rx, tx)
    if edit or not (self.alex_lpf_f1 <= fmax < self.alex_lpf_f2):    # Within same LP filter?
      self.alex_lpf_f1, self.alex_lpf_f2, rx, tx = self.FreqAlexFilters(fmax, self.conf.AlexLPF, self.conf.AlexLPF_TxEn)
      if DEBUG: print ("Change LP filter fmax, f1, f2", fmax, self.alex_lpf_f1, self.alex_lpf_f2, "rx, tx", rx, tx)
      QS.set_alex_lpf(rx, tx)
  def FreqAlexFilters(self, tx_freq, filt, enabl):
    # Find the new frequency band
    gap1 = 0.0        # If we are in a gap in the filter frequencies, this is f1 and f2 for the gap.
    gap2 = 1E20
    for f1, f2, rx, tx in filt:  # f1 and f2 are strings in MHz
      try:
        f1 = float(f1) * 1E6
        f2 = float(f2) * 1E6
      except:
        continue
      if f1 >= f2:
        continue
      if f1 <= tx_freq < f2:
        if not enabl:
          tx = rx
        return f1, f2, rx, tx
      if tx_freq >= f2 and f2 > gap1:
        gap1 = f2
      if tx_freq <= f1 and f1 < gap2:
        gap2 = f1
    return gap1, gap2, 0, 0
  def Freq2Phase(self, freq=None):		# Return the phase increment as calculated by the FPGA
    # This code attempts to duplicate the calculation of phase increment in the FPGA code.
    clock = ((int(self.conf.rx_udp_clock) + 24000) // 48000) * 48000		# this assumes the nominal clock is a multiple of 48kHz
    M2 = 2 ** 57 // clock
    M3 = 2 ** 24
    if freq is None:
      freqcomp = int(self.vfo_frequency - self.transverter_offset) * M2 + M3
    else:
      freqcomp = int(freq) * M2 + M3
    phase = (freqcomp // 2 ** 25) & 0xFFFFFFFF
    return phase
  def ReturnVfoFloat(self, freq=None):	# Return the accurate VFO as a float
    phase = self.Freq2Phase(freq)
    freq = float(phase) * self.conf.rx_udp_clock / 2.0**32
    return freq
  def ReturnFrequency(self):	# Return the current tuning and VFO frequency
    return None, None			# frequencies have not changed
  def HeartBeat(self):
    self.hermes_temperature, self.hermes_fwd_power, self.hermes_rev_power, self.hermes_pa_current = QS.get_hermes_TFRC()
    if self.application.bottom_widgets:
      self.application.bottom_widgets.UpdateText()
  def RepeaterOffset(self, offset=None):	# Change frequency for repeater offset during Tx
    if offset is None:		# Return True if frequency change is complete
      if time.time() > self.repeater_time0 + self.repeater_delay:
        return True
    elif offset == 0:			# Change back to the original frequency
      if self.repeater_freq is not None:
        self.repeater_time0 = time.time()
        self.ChangeFrequency(self.repeater_freq, self.vfo_frequency, 'repeater')
        self.repeater_freq = None
    else:			# Shift to repeater input frequency
      self.repeater_freq = self.tx_frequency
      offset = int(offset * 1000)	# Convert kHz to Hz
      self.repeater_time0 = time.time()
      self.ChangeFrequency(self.tx_frequency + offset, self.vfo_frequency, 'repeater')
    return False
  def ChangeBand(self, band):
    # band is a string: "60", "40", "WWV", etc.
    BaseHardware.ChangeBand(self, band)
    self.band = band
    self.ChangeBandFilters()
    self.SetTxLevel()
  def ChangeBandFilters(self):
    highest = self.band
    freq = self.conf.BandEdge.get(highest, (0, 0))[0]
    for pane in self.application.multi_rx_screen.receiver_list:
      f = self.conf.BandEdge.get(pane.band, (0, 0))[0]
      if freq < f:
        freq = f
        highest = pane.band
    Rx = self.conf.Hermes_BandDict.get(highest, 0)
    self.SetControlByte(0, 2, Rx << 1)		# C0 index == 0, C2[7:1]: user output
    if self.conf.Hermes_BandDictEnTx:
      Tx = self.conf.Hermes_BandDictTx.get(self.band, 0)	# Use Tx filter
    else:
      Tx = self.conf.Hermes_BandDict.get(self.band, 0)		# Use Rx filter
    QS.set_hermes_filters(Rx, Tx)
    self.ChangeAlexFilters()
  def ChangeMode(self, mode):
    # mode is a string: "USB", "AM", etc.
    BaseHardware.ChangeMode(self, mode)
    self.mode = mode
    self.SetTxLevel()
  def OnButtonPTT(self, event):
    btn = event.GetEventObject()
    if btn.GetValue():
      QS.set_PTT(1)
      QS.set_key_down(1)
    else:
      QS.set_PTT(0)
      QS.set_key_down(0)
  def OnSpot(self, level):
    # level is -1 for Spot button Off; else the Spot level 0 to 1000.
    pass
  def VarDecimGetChoices(self):		# return text labels for the control
    return self.var_rates
  def VarDecimGetLabel(self):		# return a text label for the control
    return "Sample rate ksps"
  def VarDecimGetIndex(self):		# return the current index
    return self.var_index
  def VarDecimSet(self, index=None):		# set decimation, return sample rate
    if index is None:		# initial call to set rate before the call to open()
      rate = self.application.vardecim_set		# May be None or from different hardware
    else:
      rate = int(self.var_rates[index]) * 1000
    if rate == 48000:
      self.var_index = 0
    elif rate == 96000:
      self.var_index = 1
    elif rate == 192000:
      self.var_index = 2
    elif rate == 384000:
      self.var_index = 3
    else:
      self.var_index = 0
      rate = 48000
    self.pc2hermes[0] = self.var_index		# C0 index == 0, C1[1:0]: rate
    QS.pc_to_hermes(self.pc2hermes)
    if DEBUG: print ("Change sample rate to", rate)
    return rate
  def VarDecimRange(self):
    return (48000, 384000)
  ## Hardware AGC is no longer supported in HL2 identifying as version >=40   
  def ChangeAGC(self, value):
    if value:
      self.pc2hermes[2] |= 0x10		# C0 index == 0, C3[4]: AGC enable
    else:
      self.pc2hermes[2] &= ~0x10
    QS.pc_to_hermes(self.pc2hermes)
    if DEBUG: print ("Change AGC to", value)
  ## Simpler LNA setting for HL2 identifying as version >=40, see HL2 wiki for details
  def ChangeLNA(self, value):		# LNA for Rx
    # value is -12 to +48
    if self.hermes_code_version < 40:	# Is this correct ??
      if value < 20:
        self.pc2hermes[2] |= 0x08			# C0 index == 0, C3[3]: LNA +32 dB disable == 1
        value = 19 - value
      else:
        self.pc2hermes[2] &= ~0x08		# C0 index == 0, C3[3]: LNA +32 dB enable == 0
        value = 51 - value
    else:
      value = ((value+12) & 0x3f) | 0x40
    self.pc2hermes[4 * 10 + 3] = value			# C0 index == 0x1010, C4[4:0] LNA 0-32 dB gain
    QS.pc_to_hermes(self.pc2hermes)
    if DEBUG: print ("Change LNA to", value)
  def ChangeTxLNA(self, value):		# LNA for Tx
    # value is -12 to +48
    value = ((value+12) & 0x3f) | 0x40 | 0x80
    self.SetControlByte(0x0e, 3, value)		# C0 index == 0x0e, C3
    QS.pc_to_hermes(self.pc2hermes)
    if DEBUG: print ("Change Tx LNA to", value)
  def SetTxLevel(self):
    try:
      tx_level = self.conf.tx_level[self.band]
    except KeyError:
      tx_level = self.conf.tx_level.get(None, 127)	# The default
    if self.mode[0:3] in ('DGT', 'FDV'):			# Digital modes; change power by a percentage
      reduc = self.application.digital_tx_level
    else:
      reduc = self.application.tx_level
    tx_level = int(tx_level *reduc/100.0)  
    if tx_level < 0:
      tx_level = 0
    elif tx_level > 255:
      tx_level = 255
    self.pc2hermes[4 * 9] = tx_level			# C0 index == 0x1001, C1[7:0] Tx level
    QS.pc_to_hermes(self.pc2hermes)
    if DEBUG: print("Change tx_level to", tx_level)
  def MultiRxCount(self, count):	# count == number of additional receivers besides the Tx/Rx receiver: 1, 2, 3
    # C0 index == 0, C4[5:3]: number of receivers 0b000 -> one receiver; C4[2] duplex on
    self.pc2hermes[3] = 0x04 | count << 3
    QS.pc_to_hermes(self.pc2hermes)
    if DEBUG: print("Change MultiRx count to", count)
  def MultiRxFrequency(self, index, vfo):	# index of multi rx receiver: 0, 1, 2, ...
    if DEBUG: print("Change MultiRx %d frequency to %d" % (index, vfo))
    index = index * 4 + 12		# index does not include first Tx/Rx receiver in C0 index == 1, 2
    self.pc2hermes[index    ] = vfo >> 24 & 0xff
    self.pc2hermes[index + 1] = vfo >> 16 & 0xff		# C1, C2, C3, C4: Rx freq, MSB in C1
    self.pc2hermes[index + 2] = vfo >>  8 & 0xff
    self.pc2hermes[index + 3] = vfo       & 0xff
    QS.pc_to_hermes(self.pc2hermes)
  def SetVNA(self, key_down=None, vna_start=None, vna_stop=None, vna_count=None, do_tx=False):
    if vna_count is not None:	# must be called first
      self.vna_count = vna_count
    if vna_start is None:
      start = 0
      stop = 0
    else:	# Set the start and stop frequencies and the frequency change for each point
      # vna_start and vna_stop must be specified together
      self.pc2hermes[ 4] = vna_start >> 24 & 0xff	# C0 index == 1, C1, C2, C3, C4: Tx freq, MSB in C1
      self.pc2hermes[ 5] = vna_start >> 16 & 0xff	# used for vna starting frequency
      self.pc2hermes[ 6] = vna_start >>  8 & 0xff
      self.pc2hermes[ 7] = vna_start       & 0xff
      N = self.vna_count - 1
      ph_start = self.Freq2Phase(vna_start)	# Calculate using phases
      ph_stop = self.Freq2Phase(vna_stop)
      delta = (ph_stop - ph_start + N // 2) // N
      delta = int(float(delta) * self.conf.rx_udp_clock / 2.0**32 + 0.5)
      self.pc2hermes[ 8] = delta >> 24 & 0xff		# C0 index == 2, C1, C2, C3, C4: Rx freq, MSB in C1
      self.pc2hermes[ 9] = delta >> 16 & 0xff		# used for the frequency to add for each point
      self.pc2hermes[10] = delta >>  8 & 0xff
      self.pc2hermes[11] = delta       & 0xff
      self.pc2hermes[4 * 9 + 2] = (self.vna_count >> 8) & 0xff	# C0 index == 0b1001, C3
      self.pc2hermes[4 * 9 + 3] = self.vna_count & 0xff		# C0 index == 0b1001, C4
      QS.pc_to_hermes(self.pc2hermes)
      start = self.ReturnVfoFloat(vna_start)
      phase = ph_start + self.Freq2Phase(delta) * N
      stop = float(phase) * self.conf.rx_udp_clock / 2.0**32
      start = int(start + 0.5)
      stop = int(stop + 0.5)
      if DEBUG: print ("Change VNA start", vna_start, start, "stop", vna_stop, stop, 'count', self.vna_count)
    if key_down is None:
      pass
    elif key_down:
      if not self.vna_started:
        self.vna_started = True
        self.SetControlByte(9, 2, 0x80)		# turn on VNA mode
      QS.set_PTT(1)
    else:
      QS.set_PTT(0)
    return start, stop	# Return actual frequencies after all phase rounding
  def EnablePowerAmp(self, enable):
    if enable:
      self.pc2hermes[4 * 9 + 1] |= 0x08		# C0 index == 9, C2
    else:
      self.pc2hermes[4 * 9 + 1] &= ~0x08
    QS.pc_to_hermes(self.pc2hermes)
    if DEBUG: print ("Change PwrAmp 0x%X" % (self.pc2hermes[4*9 + 1]))
  def SetLowPwrEnable(self, enable):
    if enable:
      self.pc2hermes[4 * 9 + 1] |= 0x04		# C0 index == 9, C2
    else:
      self.pc2hermes[4 * 9 + 1] &= ~0x04
    QS.pc_to_hermes(self.pc2hermes)
    if DEBUG: print ("Change LpPwrEnable 0x%X" % (self.pc2hermes[4*9 + 1]))
  def DisableSyncFreq(self, value):	# Thanks to Steve, KF7O
    if value:
      self.pc2hermes[4 * 0 + 2] |= 0x10   # C0 index == 0, C3
    else:
      self.pc2hermes[4 * 0 + 2] &= ~0x10
    QS.pc_to_hermes(self.pc2hermes)
    if DEBUG: print ("Change SyncFreq 0x%X" % (self.pc2hermes[4*0 + 2]))
  def EnableBiasChange(self, enable):
    # Bias settings are in location 12, 13, 14, 15, and are not sent unless C1 == 0x06
    if enable:
      for base in (12, 13, 14, 15):
        self.pc2hermes[4 * base] = 0x06		# C1
        self.pc2hermes[4 * base + 1] = 0xA8	# C2
      self.pc2hermes[4 * 12 + 2] = 0x00		# C3 bias 1, volitile
      self.pc2hermes[4 * 13 + 2] = 0x20		# C3 bias 1, non-volitile
      self.pc2hermes[4 * 14 + 2] = 0x10		# C3 bias 2, volitile
      self.pc2hermes[4 * 15 + 2] = 0x30		# C3 bias 2, non-volitile
    else:
      for base in (12, 13, 14, 15):
        self.pc2hermes[4 * base] = 0x00		# C1
    QS.pc_to_hermes(self.pc2hermes)
    if DEBUG: print ("Enable bias change", enable)
  ## Bias is 0 indexed to match schematic
  ## Changes for HermesLite v2 thanks to Steve, KF7O
  def ChangeBias0(self, value):
    if self.hermes_code_version >= 60: 
      i2caddr,value = 0xac,(value%256)
    else:
      i2caddr,value = 0xa8,(255-(value%256))
    self.pc2hermeslitewritequeue[0:5] = 0x7d,0x06,i2caddr,0x00,value
    self.WriteQueue(1)
    if DEBUG: print ("Change bias 0", value)
  def ChangeBias1(self, value):
    if self.hermes_code_version >= 60: 
      i2caddr,value = 0xac,(value%256)
    else:
      i2caddr,value = 0xa8,(255-(value%256))
    self.pc2hermeslitewritequeue[0:5] = 0x7d,0x06,i2caddr,0x10,value 
    self.WriteQueue(1)
    if DEBUG: print ("Change bias 1", value)
  def WriteBias(self, value0, value1):
    if self.hermes_code_version >= 60: 
      i2caddr,value0 = 0xac,(value0%256)
    else:
      i2caddr,value0 = 0xa8,(255-(value0%256))
    self.pc2hermeslitewritequeue[0:5] = 0x7d,0x06,i2caddr,0x20,value0
    self.WriteQueue(1)
    ## Wait >10ms as that is the longest EEPROM write cycle time
    time.sleep(0.015)
    value1 = (value1%256) if self.hermes_code_version >= 60 else (255-(value1%256))
    self.pc2hermeslitewritequeue[0:5] = 0x7d,0x06,i2caddr,0x30,value1 
    self.WriteQueue(1)
    ## Double write bias to EEPROM
    time.sleep(0.030)
    self.pc2hermeslitewritequeue[0:5] = 0x7d,0x06,i2caddr,0x30,value1 
    self.WriteQueue(1)    
    time.sleep(0.015)
    self.pc2hermeslitewritequeue[0:5] = 0x7d,0x06,i2caddr,0x20,value0
    self.WriteQueue(1)
    if DEBUG: print ("Write bias", value0, value1)
  def WriteQueue(self,qlen):
    ## Make sure last write(s) went through
    dt = 0.005 ## 5 ms initial delay
    while QS.get_hermeslite_writepointer() > 0:
      time.sleep(dt)
      dt *= 2
      if dt > 0.300:
        print("ERROR: Hermes-Lite write queue timeout")
        return
    ## Send next write(s)
    QS.pc_to_hermeslite_writequeue(self.pc2hermeslitewritequeue)
    QS.set_hermeslite_writepointer(qlen)
    if DEBUG:
      if dt > 0.005: print("Final dt in Hermes-Lite write queue was",dt)
  ## In HL2 firmware identifying as version >=40, AD9866 access is available
  ## See AD9866 datasheet for details, some examples:
  ## self.writeAD9866(0x08,0xff) ## Set LPF target frequency
  ## self.WriteAD9866(0x07,0x01) ## Enable RX LPF, RX to high power usage
  ## self.WriteAD9866(0x07,0x00) ## Disable RX LPF, RX to high power usage
  ## self.WriteAD9866(0x0e,0x81) ## Low digital drive strength
  ## self.WriteAD9866(0x0e,0x01) ## High digital drive strength
  ## Set RX bias to default levels
  ## cpga = 0
  ## spga = 0
  ## adcb = 0
  ## self.WriteAD9866(0x13,((cpga & 0x07) << 5) | ((spga & 0x03) << 3) | (adcb & 0x07))
  def WriteAD9866(self,addr,data):
    addr = addr & 0x01f
    data = data & 0x0ff
    self.pc2hermeslitewritequeue[0:5] = 0x7b,0x06,addr,0x00,data
    self.WriteQueue(1)
    if DEBUG: print ("Write AD9866 addr={0:06x} data={1:06x}".format(addr,data))
  def MakePowerCalibration(self):
    # Use spline interpolation to convert the ADC power sensor value to power in watts
    name = self.conf.power_meter_calib_name
    try:	# look in config file
      table = self.conf.power_meter_std_calibrations[name]
    except:
      try:	# look in local name space
        table = self.application.local_conf.GetRadioDict().get('power_meter_local_calibrations', {})[name]
      except:	# not found
        self.power_interpolator = None
        return
    if len(table) < 3:
      self.power_interpolator = None
      return
    table.sort()
    if table[0][0] > 0:		# Add zero code at zero power
      table.insert(0, [0, 0.0])
    # fill out the table to the maximum code 4095
    l = len(table) - 1
    x = table[l][0] * 1.1	# voltage increase
    y = table[l][1] * 1.1**2	# square law power increase
    while 1:
      table.append([x, y])
      if x > 4095:
        break
      x *= 1.1
      y *= 1.1**2
    self.power_interpolator = quisk_utils.SplineInterpolator(table)
  def InterpolatePower(self, x):
    if not self.power_interpolator:
      return 0.0
    y = self.power_interpolator.Interpolate(x)
    if y < 0.0:
      y = 0.0
    return y
  def VersaOut2(self, divisor):		# Use the VersaClock output 2 with a floating point divisor
    div = int(divisor * 2**24 + 0.1)
    intgr = div >> 24
    frac = (div & 0xFFFFFF) << 2
    self.WriteVersa5(0x62,0x3b)	# Clock2 CMOS1 output, 3.3V
    self.WriteVersa5(0x2c,0x00)	# Disable aux output on clock 1
    self.WriteVersa5(0x31,0x81)	# Use divider for clock2
    # Integer portion
    self.WriteVersa5(0x3d, intgr >> 4)
    self.WriteVersa5(0x3e, intgr << 4)
    # Fractional portion
    self.WriteVersa5(0x32,frac >> 24)		# [29:22]
    self.WriteVersa5(0x33,frac >> 16)		# [21:14]
    self.WriteVersa5(0x34,frac >>  8)		# [13:6]
    self.WriteVersa5(0x35,(frac & 0xFF)<<2)	# [5:0] and disable ss
    self.WriteVersa5(0x63,0x01)		# Enable clock2
  # Thanks to Steve Haynal for VersaClock code:
  def WriteVersa5(self,addr,data):
    data = data & 0x0ff
    addr = addr & 0x0ff
    ## i2caddr is 7 bits, no read write
    ## Bit 8 is set to indicate stop to HL2
    ## i2caddr = 0x80 | (0xd4 >> 1) ## ea
    self.pc2hermeslitewritequeue[0:5] = 0x7c,0x06,0xea,addr,data
    self.WriteQueue(1)
  def EnableCL2_sync76p8MHz(self):
    self.WriteVersa5(0x62,0x3b) ## Clock2 CMOS1 output, 3.3V
    self.WriteVersa5(0x2c,0x01) ## Enable aux output on clock 1
    self.WriteVersa5(0x31,0x0c) ## Use clock1 aux output as input for clock2
    self.WriteVersa5(0x63,0x01) ## Enable clock2
  def EnableCL2_61p44MHz(self):
    self.WriteVersa5(0x62,0x3b) ## Clock2 CMOS1 output, 3.3V
    self.WriteVersa5(0x2c,0x00) ## Disable aux output on clock 1
    self.WriteVersa5(0x31,0x81) ## Use divider for clock2
    ## VCO multiplier is shared for all outputs, set to 68 by firmware
    ## VCO = 38.4*68 = 2611.2 MHz
    ## There is a hardwired divide by 2 in the Versa 5 at the VCO output
    ## VCO to Dividers = 2611.2 MHZ/2 = 1305.6
    ## Target frequency of 61.44 requires dividers of 1305.6/61.44 = 21.25
    ## Frational dividers are supported
    ## Set integer portion of divider 21 = 0x15, 12 bits split across 2 registers
    self.WriteVersa5(0x3d,0x01)
    self.WriteVersa5(0x3e,0x50)
    ## Set fractional portion, 30 bits, 2**24 * .25 = 0x400000
    self.WriteVersa5(0x32,0x01) ## [29:22]
    self.WriteVersa5(0x33,0x00) ## [21:14]
    self.WriteVersa5(0x34,0x00) ## [13:6]
    self.WriteVersa5(0x35,0x00) ## [5:0] and disable ss
    self.WriteVersa5(0x63,0x01) ## Enable clock2
  def WriteEEPROM(self, addr, value):
    ## Write values into the MCP4662 EEPROM registers
    ## For example, to set a fixed IP of 192.168.33.20
    ## hw.WriteEEPROM(8,192)
    ## hw.WriteEEPROM(9,168)
    ## hw.WriteEEPROM(10,33)
    ## hw.WriteEEPROM(11,20)
    ## To set the last two values of the MAC to 55:66
    ## hw.WriteEEPROM(12,55)
    ## hw.WriteEEPROM(13,66)
    ## To enable the fixed IP and alternate MAC, and favor DHCP
    ## hw.WriteEEPROM(6, 0x80 | 0x40 | 0x20)
    ## See https://github.com/softerhardware/Hermes-Lite2/wiki/Protocol  
    if self.hermes_code_version >= 60: 
      i2caddr,value = 0xac,(value%256)
    else:
      i2caddr,value = 0xa8,(255-(value%256))
    addr = (addr << 4)%256
    self.pc2hermeslitewritequeue[0:5] = 0x7d,0x06,i2caddr,addr,value
    self.WriteQueue(1)
    if DEBUG: print ("Write EEPROM", addr, value)
  def ReadEEPROM(self, addr):
    ## To read the bias settings for bias0 and bias1
    ## hw.ReadEEPROM(2)
    ## hw.ReadEEPROM(3)
    if self.hermes_code_version >= 60: 
      i2caddr = 0xac
    else:
      i2caddr = 0xa8
    faddr = ((addr << 4)%256) | 0xc
    QS.clear_hermeslite_response()
    self.pc2hermeslitewritequeue[0:5] = 0x7d,0x07,i2caddr,faddr,0
    self.WriteQueue(1)
    for j in range(50):
      time.sleep(0.001)
      resp = QS.get_hermeslite_response()
      ##print("RESP:",j,resp[0],resp[1],resp[2],resp[3],resp[4])
      if resp[0] != 0: break
    if resp[0] == 0:
      if DEBUG: print("EEPROM read did not return a value")
      return -1
    else:
      ## MCP4662 does not autoincrement when reading 8 bytes
      ## MCP4662 stores 9 bit values, msb came first and is in lower order byte
      v0 = (resp[4] << 8) | resp[3]
      v1 = (resp[2] << 8) | resp[1]
      if (resp[0] >> 1) != 0x7d:
        ## Response mismatch
        if DEBUG: print("EEPROM read response mismatch",resp[0] >> 1)
        return -1
      elif v0 != v1:
        if DEBUG: print("EEPROM read values do not agree",v0,v1)
        return -1
      else:
        if DEBUG: print("EEPROM read {0:#x} from address {1:#x}".format(v0,addr))
      return v0
  def ProgramGateware(self, event):	# Program the Gateware (FPGA firmware) over Ethernet
    title = "Program the Gateware"
    main_frame = self.application.main_frame
    dlg = wx.FileDialog(main_frame, message='Choose an RBF file for programming the Gateware',
         style=wx.FD_OPEN, wildcard="RBF files (*.rbf)|*.rbf")
    if dlg.ShowModal() == wx.ID_OK:
      path = dlg.GetPath()
      dlg.Destroy()
    else:
      dlg.Destroy()
      return
    timeout = 0.2	# socket timeout in seconds
    erase_time = 50	# in units of timeout
    hermes_ip = self.hermes_ip
    hermes_mac = self.hermes_mac
    if not hermes_ip:
      msg = wx.MessageDialog(main_frame, "No Hermes hardware was found.", title, wx.OK|wx.ICON_ERROR)
      msg.ShowModal()
      msg.Destroy()
      return
    try:
      fp = open(path, "rb")
      size = os.stat(path).st_size
    except:
      msg = wx.MessageDialog(main_frame, "Can not read the RBF file specified.", title, wx.OK|wx.ICON_ERROR)
      msg.ShowModal()
      msg.Destroy()
      return
    for i in range(10):
      state = QS.set_params(hermes_pause=1)
      #print ("state", state)
      if state == 23:
        break
      else:
        time.sleep(0.05)
    else:
      msg = wx.MessageDialog(main_frame, "Failure to find a running Hermes and stop the samples.", title, wx.OK|wx.ICON_ERROR)
      msg.ShowModal()
      msg.Destroy()
      fp.close()
      return
    blocks = (size + 255) // 256
    dlg = wx.ProgressDialog(title, "Erase old program...", blocks + 1, main_frame, wx.PD_APP_MODAL)
    program_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    program_socket.settimeout(timeout)
    port = self.conf.rx_udp_port
    program_socket.connect((hermes_ip, port))
    cmd = bytearray(64)		# Erase command
    cmd[0] = 0xEF
    cmd[1] = 0xFE
    cmd[2] = 0x03
    cmd[3] = 0x02
    program_socket.send(cmd)
    success = False
    for i in range(erase_time):
      dlg.Update(i * blocks // erase_time)
      try:
        reply = program_socket.recv(1500)
      except socket.timeout:
        pass
      else:
        reply = bytearray(reply)
        if reply[0:3] == bytearray(b"\xEF\xFE\03") and reply[3:9] == hermes_mac:
          success = True
          break
    if not success:
      dlg.Destroy()
      self.application.Yield()
      fp.close()
      msg = wx.MessageDialog(main_frame, "Failure to erase the old program. Please push the Program button again.", title, wx.OK|wx.ICON_ERROR)
      msg.ShowModal()
      msg.Destroy()
      program_socket.close()
      return
    dlg.Update(0, "Programming...")
    cmd = bytearray(8)
    cmd[0] = 0xEF
    cmd[1] = 0xFE
    cmd[2] = 0x03
    cmd[3] = 0x01
    cmd[4] = (blocks >> 24) & 0xFF
    cmd[5] = (blocks >> 16) & 0xFF
    cmd[6] = (blocks >>  8) & 0xFF
    cmd[7] = (blocks      ) & 0xFF
    for block in range(blocks):
      dlg.Update(block)
      prog = fp.read(256)
      if block == blocks - 1:	# last block may have an odd number of bytes
        prog = prog + bytearray(b"\xFF" * (256 - len(prog)))
      if len(prog) != 256:
        print ("read wrong number of bytes for block", block)
        success = False
        break
      try:
        program_socket.send(cmd + prog)
        reply = program_socket.recv(1500)
      except socket.timeout:
        print ("Socket timeout while programming block", block)
        success = False
        break
      else:
        reply = bytearray(reply)
        if reply[0:3] != bytearray(b"\xEF\xFE\04") or reply[3:9] != hermes_mac:
          print ("Program failed at block", block)
          success = False
          break
    fp.close()
    for i in range(10):         # throw away extra packets
      try:
        program_socket.recv(1500)
      except socket.timeout:
        break
    if success:
      dlg.Update(0, "Waiting for Hermes to start...")
      wait_secs = 15    # number of seconds to wait for restart
      cmd = bytearray(63)       # Discover
      cmd[0] = 0xEF
      cmd[1] = 0xFE
      cmd[2] = 0x02
      program_socket.settimeout(1.0)
      for i in range(wait_secs):
        dlg.Update(i * blocks // wait_secs)
        if i < 5:
          time.sleep(1.0)
          continue
        program_socket.send(cmd)
        try:
          reply = program_socket.recv(1500)
        except socket.timeout:
          pass
        else:
          reply = bytearray(reply)
          #print ("0x%X 0x%X %d 0x%X 0x%X 0x%X 0x%X 0x%X 0x%X %d %d" % tuple(reply[0:11]))
          if reply[0] == 0xEF and reply[1] == 0xFE and reply[10] == 6:
            self.hermes_mac = reply[3:9]
            self.hermes_code_version = reply[9]
            st = 'Capture from Hermes device: Mac %2x:%2x:%2x:%2x:%2x:%2x, Code version %d, ID %d' % tuple(reply[3:11])
            st += ', IP %s' % self.hermes_ip
            self.config_text = st
            #print (st)
            self.application.config_text = st
            self.application.main_frame.SetConfigText(st)
            QS.set_params(hermes_pause=0)
            break
      dlg.Destroy()
      self.application.Yield()
    else:
      dlg.Destroy()
      self.application.Yield()
      msg = wx.MessageDialog(main_frame, "Programming failed. Please push the Program button again.", title, wx.OK|wx.ICON_ERROR)
      msg.ShowModal()
      msg.Destroy()
    program_socket.close()
