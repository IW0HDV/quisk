# This is the hardware file to support radios accessed by the PerseusSDR interface.

from __future__ import print_function
from __future__ import absolute_import
from __future__ import division

import socket, traceback, time, math
import _quisk as QS
try:
  from perseuspkg import perseus
except:
  #traceback.print_exc()
  perseus = None
  print ("EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE\n")

from quisk_hardware_model import Hardware as BaseHardware

DEBUG = 1

class Hardware(BaseHardware):
  def __init__(self, app, conf):
    BaseHardware.__init__(self, app, conf)
    self.vardecim_index = 0
    self.fVFO = 0.0	# Careful, this is a float
    #print ("__init__: " % conf)
    perseus = True
    self.decimation = [48000, 96000, 192000, 384000]
    self.current_dec = 192000

  def pre_open(self):
    print ("pre_open")
    pass

  def set_parameter(self, *args):
    pass

  def open(self):	# Called once to open the Hardware
      
    if not perseus:
      return "Perseus module not available"

    txt = perseus.open_device("perseus",2,3)
    print ("OOOOOOOOOOOOOOOOOOOOOOO\n")
 
#    
#    radio_dict = self.application.local_conf.GetRadioDict()
#    device = radio_dict.get('soapy_device', '')
#    if radio_dict.get('soapy_enable_tx', '') == "Enable":
#      txt = soapy.open_device(device, 1, self.conf.data_poll_usec)
#    else:
#      txt = soapy.open_device(device, 3, self.conf.data_poll_usec)	# Tx is disabled
#    for name in ('soapy_setAntenna_rx', 'soapy_setAntenna_tx'):
#      value = radio_dict.get(name, '')		# string values
#      self.set_parameter(name, value, 0.0)
#    for name in ('soapy_setBandwidth_rx', 'soapy_setBandwidth_tx', 'soapy_setSampleRate_rx', 'soapy_setSampleRate_tx'):
#      value = radio_dict.get(name, '')
#      try:
#        value = float(value) * 1E3	# these are in KHz
#      except:
#        pass
#      else:
#        self.set_parameter(name, '', value)
#        if name == 'soapy_setSampleRate_tx':
#          value = int(value + 0.1)
#          QS.set_tx_audio(tx_sample_rate=value)
#    self.ChangeGain('_rx')
#    self.ChangeGain('_tx')
#    #for name in ('soapy_getSampleRate_rx', 'soapy_getSampleRate_tx', 'soapy_getBandwidth_rx', 'soapy_getBandwidth_tx'):
#    #  print ('Get ***', name, soapy.get_parameter(name, 1))
    return txt

  def ChangeGain(self, rxtx):	# rxtx is '_rx' or '_tx'
    if not perseus:
      return
    print ("ChangeGain", rxtx)
    pass

#    radio_dict = self.application.local_conf.GetRadioDict()
#    gain_mode = radio_dict['soapy_gain_mode' + rxtx]
#    gain_values = radio_dict['soapy_gain_values' + rxtx]
#    if gain_mode == 'automatic':
#      self.set_parameter('soapy_setGainMode' + rxtx, 'true', 0.0)
#    elif gain_mode == 'total':
#      self.set_parameter('soapy_setGainMode' + rxtx, 'false', 0.0)
#      gain = gain_values.get('total', '0')
#      gain = float(gain)
#      self.set_parameter('soapy_setGain' + rxtx, '', gain)
#    elif gain_mode == 'detailed':
#      self.set_parameter('soapy_setGainMode' + rxtx, 'false', 0.0)
#      for name, dmin, dmax, dstep in radio_dict.get('soapy_listGainsValues' + rxtx, ()):
#        if name == 'total':
#          continue
#        gain = gain_values.get(name, '0')
#        gain = float(gain)
#        self.set_parameter('soapy_setGainElement' + rxtx, name, gain)

  def close(self):			# Called once to close the Hardware
    if perseus:
      perseus.close_device(1)

  def ChangeFrequency(self, tune, vfo, source='', band='', event=None):
    fVFO = float(vfo)
    if self.fVFO != fVFO:
      self.fVFO = fVFO
      perseus.set_frequency(fVFO)
    #self.set_parameter('soapy_setFrequency_tx', '', float(tune))
    return tune, vfo


  def ReturnFrequency(self):
    # Return the current tuning and VFO frequency.  If neither have changed,
    # you can return (None, None).  This is called at about 10 Hz by the main.
    # return (tune, vfo)	# return changed frequencies
    return None, None		# frequencies have not changed

  def ReturnVfoFloat(self):
    # Return the accurate VFO frequency as a floating point number.
    # You can return None to indicate that the integer VFO frequency is valid.
    return self.fVFO

  def OnBtnFDX(self, fdx):	# fdx is 0 or 1
    pass
    #if soapy:
    #  self.set_parameter('soapy_FDX', '', float(fdx))

  def OnButtonPTT(self, event):
    pass
#    btn = event.GetEventObject()
#    if btn.GetValue():
#      QS.set_PTT(1)
#      QS.set_key_down(1)
#    else:
#      QS.set_PTT(0)
#      QS.set_key_down(0)

  def OnSpot(self, level):
    # level is -1 for Spot button Off; else the Spot level 0 to 1000.
    pass

  def ChangeMode(self, mode):		# Change the tx/rx mode
    # mode is a string: "USB", "AM", etc.
    pass

  def ChangeBand(self, band):
    print ("perseus: band: %s" % band)
    pass
    # band is a string: "60", "40", "WWV", etc.
    #try:
    #  self.transverter_offset = self.conf.bandTransverterOffset[band]
    #except:
    #  self.transverter_offset = 0

  def HeartBeat(self):	# Called at about 10 Hz by the main
    pass

  def ImmediateChange(self, name, value):
    print ("ImmediateChange: perseus: name: %s value: %s" % (name, value))
    if name == 'perseus_setSampleRate_rx':
          value = int(value)
          self.application.OnBtnDecimation(rate=value)
          perseus.set_sampling_rate(value)
          self.curren_dec = value
          
#    if name in ('soapy_gain_mode_rx', 'soapy_gain_mode_tx'):
#      self.ChangeGain(name[-3:])
#    elif name in ('soapy_setAntenna_rx', 'soapy_setAntenna_tx'):
#      self.set_parameter(name, value, 0.0)	# string values
#    elif name in ('soapy_setBandwidth_rx', 'soapy_setBandwidth_tx', 'soapy_setSampleRate_rx', 'soapy_setSampleRate_tx'):
#      try:
#        value = float(value) * 1E3	# kHz values
#      except:
#        pass
#      else:
#        self.set_parameter(name, '', value)
#        if name == 'soapy_setSampleRate_tx':
#          value = int(value + 0.1)
#          QS.set_tx_audio(tx_sample_rate=value)
#        elif name == 'soapy_setSampleRate_rx':
#          value = int(value + 0.1)
#          self.application.OnBtnDecimation(rate=value)
#          self.set_parameter('soapy_setFrequency_rx', '', self.fVFO)	# driver Lime requires reset of Rx freq on sample rate change
  # The "VarDecim" methods are used to change the hardware decimation rate.
  # If VarDecimGetChoices() returns any False value, no other methods are called.

  def VarDecimGetChoices(self):	# Not used to set sample rate
      print ("VarDecimGetChoices")
      return ["48000", "96000", "192000", "384000"]
      return ["None"]

  def VarDecimGetLabel(self):	# Return a text label for the decimation control.
    print ("VarDecimGetLabel")
    return 'Rx rate: Use PerseusSDR config'

  def VarDecimGetIndex(self):	# Return the index 0, 1, ... of the current decimation.
      for i in range(len(self.decimation)):
          if self.decimation[i] == self.current_dec:
              return i
      return 0

  def VarDecimSet(self, index=None):	# Called when the control is operated; if index==None, called on startup.
     
      print ("VarDecimSet: index: %s" % (index))

      name = 'perseus_setSampleRate_rx'
      radio_dict = self.application.local_conf.GetRadioDict()
      rate = radio_dict.get(name, 48)	# this is in KHz
      try:
         print (rate)
         rate = float(self.decimation[index])
         #rate = int(rate + 0.1)
         self.current_dec = rate
      except:
         rate = self.current_dec

      print (rate)
      perseus.set_sampling_rate(int(rate))
      return int(rate)

      
      if index == None:
          return self.current_dec
      else:
          self.current_dec = self.decimation[index]
          return self.decimation[index]
#    
#    name = 'soapy_setSampleRate_rx'
#    radio_dict = self.application.local_conf.GetRadioDict()
#    rate = radio_dict.get(name, 48)	# this is in KHz
#    try:
#      rate = float(rate) * 1E3
#      rate = int(rate + 0.1)
#    except:
#      rate = 48000
#    return rate
  def VarDecimRange(self):  # Return the lowest and highest sample rate.
        print ("VarDecimRange: 48000, 96000, 192000, 384000")
        return 48000, 96000, 192000, 384000
