
from __future__ import print_function
from __future__ import absolute_import
from __future__ import division

import sys, wx, wx.lib, os, re, pickle, traceback, json
# Quisk will alter quisk_conf_defaults to include the user's config file.
import quisk_conf_defaults as conf
import _quisk as QS
from quisk_widgets import QuiskPushbutton, QuiskBitField
from quisk_widgets import wxVersion
if wxVersion in ('2', '3'):
  import wx.combo as wxcombo
else:
  wxcombo = wx                  # wxPython Phoenix
try:
  from soapypkg import soapy
except:
  soapy = None

# Settings is [
#   0: radio_requested, a string radio name or "Ask me" or "ConfigFileRadio"
#   1: radio in use and last used, a string radio name or "ConfigFileRadio"
#   2: list of radio names
#   3: parallel list of radio dicts.  These are all the parameters for the corresponding radio.  In
#      general, they are a subset of all the parameters listed in self.sections and self.receiver_data[radio_name].
#   ]

# radio_dict is a dictionary of variable names and text values for each radio including radio ConfigFileRadio.
# Only variable names from the specified radio and all sections are included. The data comes from the JSON file, and
# may be missing recently added config file items. Use GetValue() to get a configuration datum.

# local_conf is the single instance of class Configuration. conf is the configuration data from quisk_conf_defaults as
# over-writen by FSON data. 

# Increasing the software version will display a message to re-read the soapy device.
soapy_software_version = 3

def FormatKhz(dnum):	# Round to 3 decimal places; remove ending ".000"
  t = "%.3f" % dnum
  if t[-4:] == '.000':
    t = t[0:-4]
  return t

def SortKey(x):
  try:
    k = float(x)
  except:
    k = 0.0
  return k

class Configuration:
  def __init__(self, app, AskMe):	# Called first
    global application, local_conf, Settings, noname_enable, platform_ignore, platform_accept
    Settings = ["ConfigFileRadio", "ConfigFileRadio", [], []]
    application = app
    local_conf = self
    noname_enable = []
    if sys.platform == 'win32':
      platform_ignore = 'lin_'
      platform_accept = 'win_'
    else:
      platform_accept = 'lin_'
      platform_ignore = 'win_'
    self.sections = []
    self.receiver_data = []
    self.StatePath = conf.settings_file_path
    if not self.StatePath:
      self.StatePath = os.path.join(conf.DefaultConfigDir, "quisk_settings.json")
    self.ReadState()
    if AskMe == 'Same':
      pass
    elif AskMe or Settings[0] == "Ask me":
      choices = Settings[2] + ["ConfigFileRadio"]
      dlg = wx.SingleChoiceDialog(None, "", "Start Quisk with this Radio",
          choices, style=wx.DEFAULT_FRAME_STYLE|wx.OK|wx.CANCEL)
      try:
        n = choices.index(Settings[1])		# Set default to last used radio
      except:
        pass
      else:
        dlg.SetSelection(n)
      ok = dlg.ShowModal()
      if ok != wx.ID_OK:
        sys.exit(0)
      select = dlg.GetStringSelection()
      dlg.Destroy()
      if Settings[1] != select:
        Settings[1] = select
        self.settings_changed = True
    else:
      Settings[1] = Settings[0]
    if Settings[1] == "ConfigFileRadio":
      Settings[2].append("ConfigFileRadio")
      Settings[3].append({})
    self.ParseConfig()
    self.originalBandEdge = {}		# save original BandEdge
    self.originalBandEdge.update(conf.BandEdge)
  def UpdateConf(self):		# Called second to update the configuration for the selected radio
    if Settings[1] == "ConfigFileRadio":
      return
    radio_dict = self.GetRadioDict()
    radio_type = radio_dict['hardware_file_type']
    # Fill in required values
    if radio_type == "SdrIQ":
      radio_dict["use_sdriq"] = '1'
    else:
      radio_dict["use_sdriq"] = '0'
    if radio_type == "Hermes":
      radio_dict["hermes_bias_adjust"] = "False"
    if radio_type == 'SoapySDR':
      radio_dict["use_soapy"] = '1'
      self.InitSoapyNames(radio_dict)
      if radio_dict.get("soapy_file_version", 0) < soapy_software_version:
        text = "Your SoapySDR device parameters are out of date. Please go to the radio configuration screen and re-read the device parameters."
        dlg = wx.MessageDialog(None, text, 'Please Re-Read Device', wx.OK|wx.ICON_INFORMATION)
        dlg.ShowModal()
        dlg.Destroy()
    else:
      radio_dict["use_soapy"] = '0'
    if radio_type not in ("HiQSDR", "Hermes", "Red Pitaya", "Odyssey", "Odyssey2"):
      radio_dict["use_rx_udp"] = '0'
    if radio_type in ("Hermes", "Red Pitaya", "Odyssey2"):
      if "Hermes_BandDict" not in radio_dict:
        radio_dict["Hermes_BandDict"] = {}
      if "Hermes_BandDictTx" not in radio_dict:
        radio_dict["Hermes_BandDictTx"] = {}
    # fill in conf from our configuration data; convert text items to Python objects
    errors = ''
    for k, v in list(radio_dict.items()):	# radio_dict may change size during iteration
      if k == 'favorites_file_path':	# A null string is equivalent to "not entered"
        if not v.strip():
          continue
      if k in ('power_meter_local_calibrations', ):	# present in configuration data but not in the config file
        continue
      if k[0:6] == 'soapy_':	# present in configuration data but not in the config file
        continue
      if k[0:6] == 'Hware_':	# contained in hardware file, not in configuration data nor config file
        continue
      try:
        fmt = self.format4name[k]
      except:
        errors = errors + "Ignore obsolete parameter %s\n" % k
        del radio_dict[k]
        self.settings_changed = True
        continue
      k4 = k[0:4]
      if k4 == platform_ignore:
        continue
      elif k4 == platform_accept:
        k = k[4:]
      fmt4 = fmt[0:4]
      if fmt4 not in ('dict', 'list'):
        i1 = v.find('#')
        if i1 > 0:
          v = v[0:i1]
      try:
        if fmt4 == 'text':	# Note: JSON returns Unicode strings !!!
          setattr(conf, k, v)
        elif fmt4 in ('dict', 'list'):
          setattr(conf, k, v)
        elif fmt4 == 'inte':
          setattr(conf, k, int(v, base=0))
        elif fmt4 == 'numb':
          setattr(conf, k, float(v))
        elif fmt4 == 'bool':
          if v == "True":
            setattr(conf, k, True)
          else:
            setattr(conf, k, False)
        elif fmt4 == 'rfil':
          pass
        elif fmt4 == 'keyc':	# key code
          if v == "None":
            x = None
          else:
            x = eval(v)
            x = int(x)
          if k == 'hot_key_ptt2' and not isinstance(x, int):
            setattr(conf, k, wx.ACCEL_NORMAL)
          else:
            setattr(conf, k, x)
        else:
          print ("Unknown format for", k, fmt)
      except:
        errors = errors + "Failed to set %s to %s using format %s\n" % (k, v, fmt)
        #traceback.print_exc()
    if conf.color_scheme == 'B':
      conf.__dict__.update(conf.color_scheme_B)
    elif conf.color_scheme == 'C':
      conf.__dict__.update(conf.color_scheme_C)
    if errors:
      dlg = wx.MessageDialog(None, errors,
        'Update Settings', wx.OK|wx.ICON_ERROR)
      ret = dlg.ShowModal()
      dlg.Destroy()
  def InitSoapyNames(self, radio_dict):	# Set Soapy data items, but not the hardware available lists and ranges.
    if radio_dict.get('soapy_getFullDuplex_rx', 0):
      radio_dict["add_fdx_button"] = '1'
    else:
      radio_dict["add_fdx_button"] = '0'
    name = 'soapy_gain_mode_rx'
    if name not in radio_dict:
      radio_dict[name] = 'total'
    name = 'soapy_setAntenna_rx'
    if name not in radio_dict:
      radio_dict[name] = ''
    name = 'soapy_gain_values_rx'
    if name not in radio_dict:
      radio_dict[name] = {}
    name = 'soapy_gain_mode_tx'
    if name not in radio_dict:
      radio_dict[name] = 'total'
    name = 'soapy_setAntenna_tx'
    if name not in radio_dict:
      radio_dict[name] = ''
    name = 'soapy_gain_values_tx'
    if name not in radio_dict:
      radio_dict[name] = {}
  def NormPath(self, path):	# Convert between Unix and Window file paths
    if sys.platform == 'win32':
      path = path.replace('/', '\\')
    else:
      path = path.replace('\\', '/')
    return path
  def GetHardware(self):	# Called third to open the hardware file
    if Settings[1] == "ConfigFileRadio":
      return False
    path = self.GetRadioDict()["hardware_file_name"]
    path = self.NormPath(path)
    if not os.path.isfile(path):
      dlg = wx.MessageDialog(None,
        "Failure for hardware file %s!" % path,
        'Hardware File', wx.OK|wx.ICON_ERROR)
      ret = dlg.ShowModal()
      dlg.Destroy()
      path = 'quisk_hardware_model.py'
    dct = {}
    dct.update(conf.__dict__)		# make items from conf available
    if "Hardware" in dct:
      del dct["Hardware"]
    if 'quisk_hardware' in dct:
      del dct["quisk_hardware"]
    exec(compile(open(path).read(), path, 'exec'), dct)
    if "Hardware" in dct:
      application.Hardware = dct['Hardware'](application, conf)
      return True
    return False
  def Initialize(self):		# Called fourth to fill in our ConfigFileRadio radio from conf
    if Settings[1] == "ConfigFileRadio":
      radio_dict = self.GetRadioDict("ConfigFileRadio")
      typ = self.GuessType()
      radio_dict['hardware_file_type'] = typ
      all_data = []
      all_data = all_data + self.GetReceiverData(typ)
      for name, sdata in self.sections:
        all_data = all_data + sdata
      for data_name, text, fmt, help_text, values in all_data:
        data_name4 = data_name[0:4]
        if data_name4 == platform_ignore:
          continue
        elif data_name4 == platform_accept:
          conf_name = data_name[4:]
        else:
          conf_name = data_name
        try:
          if fmt in ("dict", "list"):
            radio_dict[data_name] = getattr(conf, conf_name)
          else:
            radio_dict[data_name] = str(getattr(conf, conf_name))
        except:
          if data_name == 'playback_rate':
            pass
          else:
            print ('No config file value for', data_name)
 
  def GetWidgets(self, app, hardware, conf, frame, gbs, vertBox):	# Called fifth
    if Settings[1] == "ConfigFileRadio":
      return False
    path = self.GetRadioDict()["widgets_file_name"]
    path = self.NormPath(path)
    if os.path.isfile(path):
      dct = {}
      dct.update(conf.__dict__)		# make items from conf available
      exec(compile(open(path).read(), path, 'exec'), dct)
      if "BottomWidgets" in dct:
        app.bottom_widgets = dct['BottomWidgets'](app, hardware, conf, frame, gbs, vertBox)
    return True
  def OnPageChanging(self, event):
    event.Skip()
    notebook = event.GetEventObject()
    index = event.GetSelection()
    if isinstance(notebook, RadioNotebook):	# second level notebook with pages for each radio
      if index > 0:	# First tab is already finished
        page = notebook.GetPage(index)
        page.MakeControls()
  def AddPages(self, notebk, width):	# Called sixth to add pages Help, Radios, all radio names
    global win_width
    win_width = width
    self.notebk = notebk
    page = ConfigHelp(notebk)
    notebk.AddPage(page, "Help with Radios")
    self.radio_page = Radios(notebk)
    notebk.AddPage(self.radio_page, "Radios")
    self.radios_page_start = notebk.GetPageCount()
    if sys.platform == 'win32':		# On Windows, PAGE_CHANGING doesn't work
      notebk.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self.OnPageChanging)
    else:
      notebk.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGING, self.OnPageChanging)
    for name in Settings[2]:
      page = RadioNotebook(notebk, name)
      if name == Settings[1]:
        notebk.AddPage(page, "*%s*" % name)
      else:
        notebk.AddPage(page, name)
  def GuessType(self):
    udp = conf.use_rx_udp
    if conf.use_sdriq:
      return 'SdrIQ'
    elif udp == 1:
      return 'HiQSDR'
    elif udp == 2:
      return 'HiQSDR'
    elif udp == 10:
      return 'Hermes'
    elif udp > 0:
      return 'HiQSDR'
    return 'SoftRock USB'
  def AddRadio(self, radio_name, typ):
    radio_dict = {}
    radio_dict['hardware_file_type'] = typ
    Settings[2].append(radio_name)
    Settings[3].append(radio_dict)
    for data_name, text, fmt, help_text, values in self.GetReceiverData(typ):
      radio_dict[data_name] = values[0]
    for name, data in self.sections:
      for data_name, text, fmt, help_text, values in data:
        radio_dict[data_name] = values[0]
    # Change some default values in quisk_conf_defaults.py based on radio type
    if typ in ("HiQSDR", "Hermes", "Red Pitaya", "Odyssey", "Odyssey2"):
      radio_dict["add_fdx_button"] = '1'
    page = RadioNotebook(self.notebk, radio_name)
    self.notebk.AddPage(page, radio_name)
    return True
  def RenameRadio(self, old, new):
    index = Settings[2].index(old)
    n = self.radios_page_start + index
    if old == Settings[1]:
      self.notebk.SetPageText(n, "*%s*" % new)
    else:
      self.notebk.SetPageText(n, new)
    Settings[2][index] = new
    self.notebk.GetPage(n).NewName(new)
    if old == "ConfigFileRadio":
      for ctrl in noname_enable:
        ctrl.Enable()
    return True
  def DeleteRadio(self, name):
    index = Settings[2].index(name)
    n = self.radios_page_start + index
    self.notebk.DeletePage(n)
    del Settings[2][index]
    del Settings[3][index]
    return True
  def GetRadioDict(self, radio_name=None):	# None radio_name means the current radio
    if radio_name:
      index = Settings[2].index(radio_name)
    else:	# index of radio in use
      index = Settings[2].index(Settings[1])
    return Settings[3][index]
  def GetSectionData(self, section_name):
    for sname, data in self.sections:
      if sname == section_name:
        return data
    return None
  def GetReceiverData(self, receiver_name):
    for rxname, data in self.receiver_data:
      if rxname == receiver_name:
        return data
    return None
  def GetReceiverDatum(self, receiver_name, item_name):
    for rxname, data in self.receiver_data:
      if rxname == receiver_name:
        for data_name, text, fmt, help_text, values in data:
          if item_name == data_name:
            return values[0]
        break
    return ''
  def ReceiverHasName(self, receiver_name, item_name):
    for rxname, data in self.receiver_data:
      if rxname == receiver_name:
        for data_name, text, fmt, help_text, values in data:
          if item_name == data_name:
            return True
        break
    return False
  def ReadState(self):
    self.settings_changed = False
    global Settings
    try:
      fp = open(self.StatePath, "r")
    except:
      return
    try:
      Settings = json.load(fp)
    except:
      traceback.print_exc()
    fp.close()
    try:	# Do not save settings for radio ConfigFileRadio
      index = Settings[2].index("ConfigFileRadio")
    except ValueError:
      pass
    else:
      del Settings[2][index]
      del Settings[3][index]
    for sdict in Settings[3]:		# Python None is saved as "null"
      if "tx_level" in sdict:
        if "null" in sdict["tx_level"]:
          v = sdict["tx_level"]["null"]
          sdict["tx_level"][None] = v
          del sdict["tx_level"]["null"]
  def SaveState(self):
    if not self.settings_changed:
      return
    try:
      fp = open(self.StatePath, "w")
    except:
      traceback.print_exc()
      return
    json.dump(Settings, fp, indent=2)
    fp.close()
    self.settings_changed = False
  def ParseConfig(self):
    # ParseConfig() fills self.sections, self.receiver_data, and
    # self.format4name with the items that Configuration understands.
    # Dicts and lists are Python objects.  All other items are text, not Python objects.
    #
    # Sections start with 16 #, section name
    # self.sections is a list of [section_name, section_data]
    # section_data is a list of [data_name, text, fmt, help_text, values]

    # Receiver sections start with 16 #, "Receivers ", receiver name, explain
    # self.receiver_data is a list of [receiver_name, receiver_data]
    # receiver_data is a list of [data_name, text, fmt, help_text, values]

    # Variable names start with ## variable_name   variable_text, format
    #     The format is integer, number, text, boolean, integer choice, text choice, rfile
    #     Then some help text starting with "# "
    #     Then a list of possible value#explain with the default first
    #     Then a blank line to end.

    self.format4name = {}
    self.format4name['hardware_file_type'] = 'text'
    re_AeqB = re.compile("^#?(\w+)\s*=\s*([^#]+)#*(.*)")		# item values "a = b"
    section = None
    data_name = None
    fp = open("quisk_conf_defaults.py", "r")
    for line in fp:
      line = line.strip()
      if not line:
        data_name = None
        continue
      if line[0:27] == '################ Receivers ':
        section = 'Receivers'
        args = line[27:].split(',', 1)
        rxname = args[0].strip()
        section_data = []
        self.receiver_data.append((rxname, section_data))
      elif line[0:17] == '################ ':
        args = line[17:].split(None, 2)
        section = args[0]
        if section in ('Colors', 'Obsolete'):
          section = None
          continue
        rxname = None
        section_data = []
        self.sections.append((section, section_data))
      if not section:
        continue
      if line[0:3] == '## ':		# item_name   item_text, format
        args = line[3:].split(None, 1)
        data_name = args[0]
        args = args[1].split(',', 1)
        dspl = args[0].strip()
        fmt = args[1].strip()
        value_list = []
        if data_name in self.format4name:
          if self.format4name[data_name] != fmt:
            print ("Inconsistent format for", data_name, self.format4name[data_name], fmt)
        else:
          self.format4name[data_name] = fmt
        section_data.append([data_name, dspl, fmt, '', value_list])
      if not data_name:
        continue
      mo = re_AeqB.match(line)
      if mo:
        if data_name != mo.group(1):
          print ("Parse error for", data_name)
          continue
        value = mo.group(2).strip()
        expln = mo.group(3).strip()
        if value[0] in ('"', "'"):
          value = value[1:-1]
        elif value == '{':		# item is a dictionary
          value = getattr(conf, data_name)
        elif value == '[':		# item is a list
          value = getattr(conf, data_name)
        if expln:
          value_list.append("%s # %s" % (value, expln))
        else:
          value_list.append(value)
      elif line[0:2] == '# ':
        section_data[-1][3] = section_data[-1][3] + line[2:] + ' '
    fp.close()

class ConfigHelp(wx.html.HtmlWindow):	# The "Help with Radios" first-level page
  """Create the help screen for the configuration tabs."""
  def __init__(self, parent):
    wx.html.HtmlWindow.__init__(self, parent, -1, size=(win_width, 100))
    if "gtk2" in wx.PlatformInfo:
      self.SetStandardFonts()
    self.SetFonts("", "", [10, 12, 14, 16, 18, 20, 22])
    self.SetBackgroundColour(parent.bg_color)
    # read in text from file help_conf.html in the directory of this module
    self.LoadFile('help_conf.html')

class QPowerMeterCalibration(wx.Frame):
  """Create a window to enter the power output and corresponding ADC value AIN1/2"""
  def __init__(self, parent, local_names):
    self.parent = parent
    self.local_names = local_names
    self.table = []	# calibration table: list of [ADC code, power watts]
    try:	# may be missing in wxPython 2.x
      wx.Frame.__init__(self, application.main_frame, -1, "Power Meter Calibration",
         pos=(50, 100), style=wx.CAPTION|wx.FRAME_FLOAT_ON_PARENT)
    except AttributeError:
      wx.Frame.__init__(self, application.main_frame, -1, "Power Meter Calibration",
         pos=(50, 100), style=wx.CAPTION)
    panel = wx.Panel(self)
    self.MakeControls(panel)
    self.Show()
  def MakeControls(self, panel):
    charx = panel.GetCharWidth()
    tab1 = charx * 5
    y = 20
    # line 1
    txt = wx.StaticText(panel, -1, 'Name for new calibration table', pos=(tab1, y))
    w, h = txt.GetSize().Get()
    tab2 = tab1 + w + tab1 // 2
    self.cal_name = wx.TextCtrl(panel, -1, pos=(tab2, h), size=(charx * 16, h * 13 // 10))
    y += h * 3
    # line 2
    txt = wx.StaticText(panel, -1, 'Measured power level in watts', pos=(tab1, y))
    self.cal_power = wx.TextCtrl(panel, -1, pos=(tab2, y), size=(charx * 16, h * 13 // 10))
    x = tab2 + charx * 20
    add = QuiskPushbutton(panel, self.OnBtnAdd, "Add to Table")
    add.SetPosition((x, y - h * 3 // 10))
    add.SetColorGray()
    ww, hh = add.GetSize().Get()
    width = x + ww + tab1
    y += h * 3
    # line 3
    sv = QuiskPushbutton(panel, self.OnBtnSave, "Save")
    sv.SetColorGray()
    cn = QuiskPushbutton(panel, self.OnBtnCancel, "Cancel")
    cn.SetColorGray()
    w, h = cn.GetSize().Get()
    sv.SetPosition((width // 4, y))
    cn.SetPosition((width - width // 4 - w, y))
    y += h * 12 // 10
    # help text at bottom
    wx.StaticText(panel, -1, '1. Attach a 50 ohm load and power meter to the antenna connector.', pos=(tab1, y))
    w, h = txt.GetSize().Get()
    h = h * 12 // 10
    y += h
    wx.StaticText(panel, -1, '2. Use the Spot button to transmit at a very low power.', pos=(tab1, y))
    y += h
    wx.StaticText(panel, -1, '3. Enter the measured power in the box above and press "Add to Table".', pos=(tab1, y))
    y += h
    wx.StaticText(panel, -1, '4. Increase the power a small amount and repeat step 3.', pos=(tab1, y))
    y += h
    wx.StaticText(panel, -1, '5. Increase power again and repeat step 3.', pos=(tab1, y))
    y += h
    wx.StaticText(panel, -1, '6. Keep adding measurements to the table until you reach full power.', pos=(tab1, y))
    y += h
    wx.StaticText(panel, -1, '7. Ten or twelve measurements should be enough. Then press "Save".', pos=(tab1, y))
    y += h
    wx.StaticText(panel, -1, 'To delete a table, save a table with zero measurements.', pos=(tab1, y))
    y += h * 2
    self.SetClientSize(wx.Size(width, y))
  def OnBtnCancel(self, event=None):
    self.parent.ChangePMcalFinished(None, None)
    self.Destroy()
  def OnBtnSave(self, event):
    name = self.cal_name.GetValue().strip()
    if not name:
      dlg = wx.MessageDialog(self,
        'Please enter a name for the new calibration table.',
        'Missing Name', wx.OK|wx.ICON_ERROR)
      dlg.ShowModal()
      dlg.Destroy()
    elif name in conf.power_meter_std_calibrations:		# known calibration names from the config file
      dlg = wx.MessageDialog(self,
        'That name is reserved. Please enter a different name.',
        'Reserved Name', wx.OK|wx.ICON_ERROR)
      dlg.ShowModal()
      dlg.Destroy()
    elif name in self.local_names:
      if self.table:
        dlg = wx.MessageDialog(self,
          'That name exists. Replace the existing table?',
          'Replace Table', wx.OK|wx.CANCEL|wx.ICON_EXCLAMATION)
        ret = dlg.ShowModal()
        dlg.Destroy()
        if ret == wx.ID_OK:
          self.parent.ChangePMcalFinished(name, self.table)
          self.Destroy()
      else:
        dlg = wx.MessageDialog(self,
          'That name exists but the table is empty. Delete the existing table?.',
          'Delete Table', wx.OK|wx.CANCEL|wx.ICON_EXCLAMATION)
        ret = dlg.ShowModal()
        dlg.Destroy()
        if ret == wx.ID_OK:
          self.parent.ChangePMcalFinished(name, None)
          self.Destroy()
    else:
      self.parent.ChangePMcalFinished(name, self.table)
      self.Destroy()
  def OnBtnAdd(self, event):
    power = self.cal_power.GetValue().strip()
    self.cal_power.Clear()
    try:
      power = float(power)
    except:
      dlg = wx.MessageDialog(self, 'Missing or bad measured power.', 'Error in Power', wx.OK|wx.ICON_ERROR)
      dlg.ShowModal()
      dlg.Destroy()
    else:
      ## Convert measured voltage to power
      #power *= 6.388
      #power = power**2 / 50.0
      fwd = application.Hardware.hermes_fwd_power
      rev = application.Hardware.hermes_rev_power
      if fwd >= rev:
        self.table.append([fwd, power])		# Item must use lists; sort() will fail with mixed lists and tuples
      else:
        self.table.append([rev, power])

class ListEditDialog(wx.Dialog):	# Display a dialog with a List-Edit control, plus Ok/Cancel
  def __init__(self, parent, title, choice, choices, width):
    wx.Dialog.__init__(self, parent, title=title, style=wx.CAPTION|wx.CLOSE_BOX)
    cancel = wx.Button(self, wx.ID_CANCEL, "Cancel")
    bsize = cancel.GetSize()
    margin = bsize.height
    self.combo = wx.ComboBox(self, -1, choice, pos=(margin, margin), size=(width - margin * 2, -1), choices=choices, style=wx.CB_DROPDOWN)
    y = margin + self.combo.GetSize().height + margin
    x = width - margin * 2 - bsize.width * 2
    x = x // 3
    ok = wx.Button(self, wx.ID_OK, "OK", pos=(margin + x, y))
    cancel.SetPosition((width - margin - x - bsize.width, y))
    self.SetClientSize(wx.Size(width, y + bsize.height * 14 // 10))
  def GetValue(self):
    return self.combo.GetValue()

class RadioNotebook(wx.Notebook):	# The second-level notebook for each radio name
  def __init__(self, parent, radio_name):
    wx.Notebook.__init__(self, parent)
    font = wx.Font(conf.config_font_size, wx.FONTFAMILY_SWISS, wx.NORMAL,
          wx.FONTWEIGHT_NORMAL, False, conf.quisk_typeface)
    self.SetFont(font)
    self.SetBackgroundColour(parent.bg_color)
    self.radio_name = radio_name
    self.pages = []
    radio_dict = local_conf.GetRadioDict(radio_name)
    radio_type = radio_dict['hardware_file_type']
    if radio_type == 'SoapySDR':
      page = RadioHardwareSoapySDR(self, radio_name)
    else:
      page = RadioHardware(self, radio_name)
    self.AddPage(page, "Hardware")
    self.pages.append(page)
    page = RadioSound(self, radio_name)
    self.AddPage(page, "Sound")
    self.pages.append(page)
    for section, names in local_conf.sections:
      if section in ('Sound', 'Bands', 'Filters'):		# There is a special page for these sections
        continue
      page = RadioSection(self, radio_name, section, names)
      self.AddPage(page, section)
      self.pages.append(page)
    page = RadioBands(self, radio_name)
    self.AddPage(page, "Bands")
    self.pages.append(page)
    if "use_rx_udp" in radio_dict and radio_dict["use_rx_udp"] == '10':
      page = RadioFilters(self, radio_name)
      self.AddPage(page, "Filters")
      self.pages.append(page)
  def NewName(self, new_name):
    self.radio_name = new_name
    for page in self.pages:
      page.radio_name = new_name

class ComboCtrl(wxcombo.ComboCtrl):
  def __init__(self, parent, value, choices, no_edit=False):
    self.value = value
    self.choices = choices[:]
    self.handler = None
    self.height = parent.quisk_height
    if no_edit:
      wxcombo.ComboCtrl.__init__(self, parent, -1, style=wx.CB_READONLY)
    else:
      wxcombo.ComboCtrl.__init__(self, parent, -1, style=wx.TE_PROCESS_ENTER)
      self.GetTextCtrl().Bind(wx.EVT_KILL_FOCUS, self.OnKillFocus)
      self.Bind(wx.EVT_TEXT_ENTER, self.OnTextEnter)
    self.ctrl = ListBoxComboPopup(choices, parent.font)
    self.SetPopupControl(self.ctrl)
    self.SetText(value)
    self.SetSizes()
  def SetItems(self, lst):
    self.ctrl.SetItems(lst)
    self.choices = lst[:]
    self.SetSizes()
  def SetSizes(self):
    charx = self.GetCharWidth()
    wm = charx
    w, h = self.GetTextExtent(self.value)
    if wm < w:
      wm = w
    for ch in self.choices:
      w, h = self.GetTextExtent(ch)
      if wm < w:
        wm = w
    wm += charx * 5
    self.SetSizeHints(wm, self.height, 9999, self.height)
  def SetSelection(self, n):
    try:
      text = self.choices[n]
    except IndexError:
      self.SetText('')
      self.value = ''
    else:
      self.ctrl.SetSelection(n)
      self.SetText(text)
      self.value = text
  def OnTextEnter(self, event=None):
    if event:
      event.Skip()
    if self.value != self.GetValue():
      self.value = self.GetValue()
      if self.handler:
        ok = self.handler(self)
  def OnKillFocus(self, event):
    event.Skip()
    self.OnTextEnter(event)
  def OnListbox(self):
    self.OnTextEnter()

class ListBoxComboPopup(wxcombo.ComboPopup):
  def __init__(self, choices, font):
    wxcombo.ComboPopup.__init__(self)
    self.choices = choices
    self.font = font
    self.lbox = None
  def Create(self, parent):
    self.lbox = wx.ListBox(parent, choices=self.choices, style=wx.LB_SINGLE)
    self.lbox.SetFont(self.font)
    self.lbox.Bind(wx.EVT_MOTION, self.OnMotion)
    self.lbox.Bind(wx.EVT_LEFT_DOWN, self.OnLeftDown)
    return True
  def SetItems(self, lst):
    self.choices = lst[:]
    self.lbox.Set(self.choices)
  def SetSelection(self, n):
    self.lbox.SetSelection(n)
  def GetStringValue(self):
    try:
      return self.choices[self.lbox.GetSelection()]
    except IndexError:
      pass
    return ''
  def GetAdjustedSize(self, minWidth, prefHeight, maxHeight):
    chary = self.lbox.GetCharHeight()
    return (minWidth, chary * len(self.choices) * 15 // 10 + chary)
  def OnLeftDown(self, event):
    event.Skip()
    self.Dismiss()
    if wxVersion in ('2', '3'):
      self.GetCombo().OnListbox()
    else:
      self.GetComboCtrl().OnListbox()
  def OnMotion(self, event):
    event.Skip()
    item = self.lbox.HitTest(event.GetPosition())
    if item >= 0:
      self.lbox.SetSelection(item)
  def GetControl(self):
    return self.lbox

class BaseWindow(wx.ScrolledWindow):
  def __init__(self, parent):
    wx.ScrolledWindow.__init__(self, parent)
    self.font = wx.Font(conf.config_font_size, wx.FONTFAMILY_SWISS, wx.NORMAL,
          wx.FONTWEIGHT_NORMAL, False, conf.quisk_typeface)
    self.SetFont(self.font)
    self.row = 1
    self.charx = self.GetCharWidth()
    self.chary = self.GetCharHeight()
    self.quisk_height = self.chary * 14 // 10
    # GBS
    self.gbs = wx.GridBagSizer(2, 2)
    self.gbs.SetEmptyCellSize((self.charx, self.charx))
    self.SetSizer(self.gbs)
    self.gbs.Add((self.charx, self.charx), (0, 0))
  def MarkCols(self):
    for col in range(1, self.num_cols):
      c = wx.StaticText(self, -1, str(col % 10))
      self.gbs.Add(c, (self.row, col))
    self.row += 1
  def NextRow(self, row=None):
    if row is None:
      self.row += 1
    else:
      self.row = row
  def AddTextL(self, col, text, span=None):
    c = wx.StaticText(self, -1, text)
    if col < 0:
      pass
    elif span is None:
      self.gbs.Add(c, (self.row, col), flag=wx.ALIGN_CENTER_VERTICAL)
    else:
      self.gbs.Add(c, (self.row, col), span=(1, span), flag=wx.ALIGN_CENTER_VERTICAL)
    return c
  def AddTextC(self, col, text, span=None, flag=wx.ALIGN_CENTER):
    c = wx.StaticText(self, -1, text)
    if col < 0:
      pass
    elif span is None:
      self.gbs.Add(c, (self.row, col), flag=flag)
    else:
      self.gbs.Add(c, (self.row, col), span=(1, span), flag=flag)
    return c
  def AddTextCHelp(self, col, text, help_text, span=None):
    bsizer = wx.BoxSizer(wx.HORIZONTAL)
    txt = wx.StaticText(self, -1, text)
    bsizer.Add(txt, flag=wx.ALIGN_CENTER_VERTICAL)
    btn = QuiskPushbutton(self, self._BTnHelp, "..")
    btn.SetColorGray()
    btn.quisk_help_text = help_text
    btn.quisk_caption = text
    h = self.quisk_height + 2
    btn.SetSizeHints(h, h, h, h)
    bsizer.Add(btn, flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT|wx.RIGHT, border=self.charx)
    if col < 0:
      pass
    elif span is None:
      self.gbs.Add(bsizer, (self.row, col), flag = wx.ALIGN_CENTER)
    else:
      self.gbs.Add(bsizer, (self.row, col), span=(1, span), flag = wx.ALIGN_CENTER)
    return bsizer
  def AddTextLHelp(self, col, text, help_text, span=None):
    bsizer = wx.BoxSizer(wx.HORIZONTAL)
    btn = QuiskPushbutton(self, self._BTnHelp, "..")
    btn.SetColorGray()
    btn.quisk_help_text = help_text
    btn.quisk_caption = text
    h = self.quisk_height + 2
    btn.SetSizeHints(h, h, h, h)
    bsizer.Add(btn, flag=wx.ALIGN_CENTER_VERTICAL|wx.LEFT|wx.RIGHT, border=self.charx)
    txt = wx.StaticText(self, -1, text)
    bsizer.Add(txt, flag=wx.ALIGN_CENTER_VERTICAL)
    if col < 0:
      pass
    elif span is None:
      self.gbs.Add(bsizer, (self.row, col), flag = wx.ALIGN_LEFT)
    else:
      self.gbs.Add(bsizer, (self.row, col), span=(1, span), flag = wx.ALIGN_LEFT)
    return bsizer
  def AddTextEditHelp(self, col, text1, text2, help_text, border=2, span1=1, span2=1):
    txt = wx.StaticText(self, -1, text1)
    self.gbs.Add(txt, (self.row, col), span=(1, span1), flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self.charx)
    col += span1
    #txt = wx.StaticText(self, -1, text2)
    edt = wx.TextCtrl(self, -1, text2, style=wx.TE_READONLY)
    #self.gbs.Add(txt, (self.row, col), span=(1, span2), flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self.charx)
    self.gbs.Add(edt, (self.row, col), span=(1, span2),
       flag=wx.ALIGN_RIGHT|wx.ALIGN_CENTER_VERTICAL|wx.EXPAND|wx.RIGHT,
       border=self.charx*2//10)
    col += span2
    btn = QuiskPushbutton(self, self._BTnHelp, "..")
    btn.SetColorGray()
    btn.quisk_help_text = help_text
    btn.quisk_caption = text1
    h = self.quisk_height + 2
    btn.SetSizeHints(h, h, h, h)
    self.gbs.Add(btn, (self.row, col), flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self.charx*border)
    return txt, edt, btn
  def AddTextButtonHelp(self, col, text, butn_text, handler, help_text):
    border = 1
    txt = wx.StaticText(self, -1, text)
    self.gbs.Add(txt, (self.row, col), flag = wx.ALIGN_LEFT)
    btn = QuiskPushbutton(self, handler, butn_text)
    btn.SetColorGray()
    h = self.quisk_height + 2
    btn.SetSizeHints(-1, h, -1, h)
    self.gbs.Add(btn, (self.row, col + 1), flag = wx.ALIGN_RIGHT|wx.EXPAND)
    hbtn = QuiskPushbutton(self, self._BTnHelp, "..")
    hbtn.SetColorGray()
    hbtn.quisk_help_text = help_text
    hbtn.quisk_caption = text
    h = self.quisk_height + 2
    hbtn.SetSizeHints(h, h, h, h)
    self.gbs.Add(hbtn, (self.row, col + 2), flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self.charx*border)
    return txt, btn
  def AddTextCtrl(self, col, text, handler=None, span=None):
    c = wx.TextCtrl(self, -1, text, style=wx.TE_RIGHT)
    if col < 0:
      pass
    elif span is None:
      self.gbs.Add(c, (self.row, col), flag=wx.ALIGN_CENTER)
    else:
      self.gbs.Add(c, (self.row, col), span=(1, span), flag=wx.ALIGN_CENTER)
    if handler:
      c.Bind(wx.EVT_TEXT, handler)
    return c
  def AddBoxSizer(self, col, span):
    bsizer = wx.BoxSizer(wx.HORIZONTAL)
    self.gbs.Add(bsizer, (self.row, col), span=(1, span))
    return bsizer
  def AddColSpacer(self, col, width):		# add a width spacer to row 0
    self.gbs.Add((width * self.charx, 1), (0, col))		# width is in characters
  def AddRadioButton(self, col, text, span=None, start=False):
    if start:
      c = wx.RadioButton(self, -1, text, style=wx.RB_GROUP)
    else:
      c = wx.RadioButton(self, -1, text)
    if col < 0:
      pass
    elif span is None:
      self.gbs.Add(c, (self.row, col), flag=wx.ALIGN_CENTER_VERTICAL)
    else:
      self.gbs.Add(c, (self.row, col), span=(1, span), flag=wx.ALIGN_CENTER_VERTICAL)
    return c
  def AddCheckBox(self, col, text, handler=None, flag=0, border=0):
    btn = wx.CheckBox(self, -1, text)
    h = self.quisk_height + 2
    btn.SetSizeHints(-1, h, -1, h)
    if col >= 0:
      self.gbs.Add(btn, (self.row, col), flag=flag, border=border*self.charx)
    if self.radio_name == "ConfigFileRadio":
      btn.Enable(False)
      noname_enable.append(btn)
    if handler:
      btn.Bind(wx.EVT_CHECKBOX, handler)
    return btn
  def AddBitField(self, col, number, name, band, value, handler=None, span=None, border=1):
    bf = QuiskBitField(self, number, value, self.quisk_height, handler)
    if col < 0:
      pass
    elif span is None:
      self.gbs.Add(bf, (self.row, col), flag=wx.ALIGN_RIGHT|wx.ALIGN_CENTER_VERTICAL|wx.RIGHT|wx.LEFT, border=border*self.charx)
    else:
      self.gbs.Add(bf, (self.row, col), span=(1, span), flag=wx.ALIGN_RIGHT|wx.ALIGN_CENTER_VERTICAL|wx.RIGHT|wx.LEFT, border=border*self.charx)
    bf.quisk_data_name = name
    bf.quisk_band = band
    return bf
  def AddPushButton(self, col, text, handler, border=0):
    btn = QuiskPushbutton(self, handler, text)
    btn.SetColorGray()
    h = self.quisk_height + 2
    btn.SetSizeHints(-1, h, -1, h)
    if col >= 0:
      self.gbs.Add(btn, (self.row, col), flag=wx.RIGHT|wx.LEFT, border=border*self.charx)
    if self.radio_name == "ConfigFileRadio":
      btn.Enable(False)
      noname_enable.append(btn)
    return btn
  def AddPushButtonR(self, col, text, handler, border=0):
    btn = self.AddPushButton(-1, text, handler, border)
    if col >= 0:
      self.gbs.Add(btn, (self.row, col), flag=wx.ALIGN_RIGHT|wx.RIGHT|wx.LEFT, border=border*self.charx)
    return btn
  def AddComboCtrl(self, col, value, choices, right=False, no_edit=False, span=None, border=1):
    cb = ComboCtrl(self, value, choices, no_edit)
    if col < 0:
      pass
    elif span is None:
      self.gbs.Add(cb, (self.row, col), flag=wx.ALIGN_RIGHT|wx.ALIGN_CENTER_VERTICAL|wx.EXPAND|wx.RIGHT|wx.LEFT, border=border*self.charx)
    else:
      self.gbs.Add(cb, (self.row, col), span=(1, span), flag=wx.ALIGN_RIGHT|wx.ALIGN_CENTER_VERTICAL|wx.EXPAND|wx.RIGHT|wx.LEFT, border=border*self.charx)
    if self.radio_name == "ConfigFileRadio":
      cb.Enable(False)
      noname_enable.append(cb)
    return cb
  def AddComboCtrlTx(self, col, text, value, choices, right=False, no_edit=False):
    c = wx.StaticText(self, -1, text)
    if col >= 0:
      self.gbs.Add(c, (self.row, col))
      cb = self.AddComboCtrl(col + 1, value, choices, right, no_edit)
    else:
      cb = self.AddComboCtrl(col, value, choices, right, no_edit)
    return c, cb
  def AddTextComboHelp(self, col, text, value, choices, help_text, no_edit=False, border=2, span_text=1, span_combo=1):
    txt = wx.StaticText(self, -1, text)
    self.gbs.Add(txt, (self.row, col), span=(1, span_text), flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self.charx)
    col += span_text
    cb = self.AddComboCtrl(-1, value, choices, False, no_edit)
    if no_edit:
      if '#' in value:
        value = value[0:value.index('#')]
      value = value.strip()
      l = len(value)
      for i in range(len(choices)):
        ch = choices[i]
        if '#' in ch:
          ch = ch[0:ch.index('#')]
        ch.strip()
        if value == ch[0:l]:
          cb.SetSelection(i)
          break
      else:
        if 'fail' in value:
          pass
        else:
          print ("Failure to set value for", text, value, choices)
    self.gbs.Add(cb, (self.row, col), span=(1, span_combo),
       flag=wx.ALIGN_RIGHT|wx.ALIGN_CENTER_VERTICAL|wx.EXPAND|wx.RIGHT,
       border=self.charx*2//10)
    col += span_combo
    btn = QuiskPushbutton(self, self._BTnHelp, "..")
    btn.SetColorGray()
    btn.quisk_help_text = help_text
    btn.quisk_caption = text
    h = self.quisk_height + 2
    btn.SetSizeHints(h, h, h, h)
    self.gbs.Add(btn, (self.row, col), flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self.charx*border)
    return txt, cb, btn
  def AddTextDblSpinnerHelp(self, col, text, value, dmin, dmax, dinc, help_text, border=2, span_text=1, span_spinner=1):
    txt = wx.StaticText(self, -1, text)
    self.gbs.Add(txt, (self.row, col), span=(1, span_text), flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self.charx)
    col += span_text
    spn = wx.SpinCtrlDouble(self, -1, initial=value, min=dmin, max=dmax, inc=dinc)
    self.gbs.Add(spn, (self.row, col), span=(1, span_spinner),
       flag=wx.ALIGN_RIGHT|wx.ALIGN_CENTER_VERTICAL|wx.EXPAND|wx.RIGHT,
       border=self.charx*2//10)
    col += span_spinner
    btn = QuiskPushbutton(self, self._BTnHelp, "..")
    btn.SetColorGray()
    btn.quisk_help_text = help_text
    btn.quisk_caption = text
    h = self.quisk_height + 2
    btn.SetSizeHints(h, h, h, h)
    self.gbs.Add(btn, (self.row, col), flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self.charx*border)
    return txt, spn, btn
  def AddTextSpinnerHelp(self, col, text, value, imin, imax, help_text, border=2, span_text=1, span_spinner=1):
    txt = wx.StaticText(self, -1, text)
    self.gbs.Add(txt, (self.row, col), span=(1, span_text), flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self.charx)
    col += span_text
    spn = wx.SpinCtrl(self, -1, "")
    spn.SetRange(imin, imax)
    spn.SetValue(value)
    self.gbs.Add(spn, (self.row, col), span=(1, span_spinner),
       flag=wx.ALIGN_RIGHT|wx.ALIGN_CENTER_VERTICAL|wx.EXPAND|wx.RIGHT,
       border=self.charx*2//10)
    col += span_spinner
    btn = QuiskPushbutton(self, self._BTnHelp, "..")
    btn.SetColorGray()
    btn.quisk_help_text = help_text
    btn.quisk_caption = text
    h = self.quisk_height + 2
    btn.SetSizeHints(h, h, h, h)
    self.gbs.Add(btn, (self.row, col), flag=wx.ALIGN_CENTER_VERTICAL|wx.RIGHT, border=self.charx*border)
    return txt, spn, btn
  def _BTnHelp(self, event):
    btn = event.GetEventObject()
    dlg = wx.MessageDialog(self, btn.quisk_help_text, btn.quisk_caption, style=wx.OK|wx.ICON_INFORMATION)
    dlg.ShowModal()
    dlg.Destroy()
  def OnChange(self, ctrl):
    value = ctrl.GetValue()
    self.OnChange2(ctrl, value)
  def OnChange2(self, ctrl, value):
    # Careful: value is Unicode
    name = ctrl.quisk_data_name
    fmt4 = local_conf.format4name[name][0:4]
    ok, x = self.EvalItem(value, fmt4)	# Only evaluates integer, number, boolean, text, rfile
    if ok:
      radio_dict = local_conf.GetRadioDict(self.radio_name)
      radio_dict[name] = value
      local_conf.settings_changed = True
      # Immediate changes
      if self.radio_name == Settings[1]:	# changed for current radio
        if name in ('hot_key_ptt_toggle', 'hot_key_ptt_if_hidden', 'keyupDelay'):
          setattr(conf, name, x)
          application.ImmediateChange(name)
        elif name == "reverse_tx_sideband":
          setattr(conf, name, x)
          QS.set_tx_audio(reverse_tx_sideband=x)
        elif name == "dc_remove_bw":
          setattr(conf, name, x)
          QS.set_sparams(dc_remove_bw=x)
        elif name == "digital_output_level":
          setattr(conf, name, x)
          QS.set_sparams(digital_output_level=x)
        elif name == 'hermes_lowpwr_tr_enable':
          application.Hardware.SetLowPwrEnable(x)
        elif name == 'hermes_power_amp':
          application.Hardware.EnablePowerAmp(x)
        elif name == 'hermes_TxLNA_dB':
          application.Hardware.ChangeTxLNA(x)
        elif name == "hermes_bias_adjust" and self.HermesBias0:
          self.HermesBias0.Enable(x)
          self.HermesBias1.Enable(x)
          self.HermesWriteBiasButton.Enable(x)
          application.Hardware.EnableBiasChange(x)
        elif name == "hermes_disable_sync":
          application.Hardware.DisableSyncFreq(x)
  def FormatOK(self, value, fmt4):		# Check formats integer, number, boolean
    ok, v = self.EvalItem(value, fmt4)
    return ok
  def EvalItem(self, value, fmt4):		# Return Python integer, number, boolean, text
    # return is (item_is_ok, evaluated_item)
    if fmt4 in ('text', 'rfil'):	# text items are always OK
      return True, value
    jj = value.find('#')
    if jj > 0:
      value = value[0:jj]
    try:	# only certain formats are evaluated
      if fmt4 == 'inte':
        v = int(value, base=0)
      elif fmt4 == 'numb':
        v = float(value)
      elif fmt4 == 'bool':
        if value == "True":
          v = True
        else:
          v = False
      else:
        return False, None
    except:
      dlg = wx.MessageDialog(None,
        "Can not set item with format %s to value %s" % (fmt4, value),
        'Change to item', wx.OK|wx.ICON_ERROR)
      dlg.ShowModal()
      dlg.Destroy()
      return False, None
    return True, v
  def GetValue(self, name, radio_dict):
    try:
      value = radio_dict[name]
    except:
      pass
    else:
      return value
    # Value was not in radio_dict.  Get it from conf.  There are values for platform win_data_name and lin_data_name.
    # The win_ and lin_ names are not in conf.
    try:
      fmt = local_conf.format4name[name]
    except:
      fmt = ''		# not all items in conf are in section_data or receiver_data
    try:
      if fmt == 'dict':				# make a copy for this radio
        value = {}
        value.update(getattr(conf, name))
      elif fmt == 'list':			# make a copy for this radio
        value = getattr(conf, name)[:]
      else:
        value = str(getattr(conf, name))
    except:
      return ''
    else:
      return value

class Radios(BaseWindow):	# The "Radios" first-level page
  def __init__(self, parent):
    BaseWindow.__init__(self, parent)
    self.SetBackgroundColour(parent.bg_color)
    self.num_cols = 8
    self.radio_name = None
    self.cur_radio_text = self.AddTextL(1, 'xx', self.num_cols - 1)
    self.SetCurrentRadioText()
    self.NextRow()
    self.NextRow()
    item = self.AddTextL(1, "When Quisk starts, use the radio")
    self.start_radio = self.AddComboCtrl(2, 'big_radio_name', choices=[], no_edit=True)
    self.start_radio.handler = self.OnChoiceStartup
    self.NextRow()
    item = self.AddTextL(1, "Add a new radio with the general type")
    choices = []
    for name, data in local_conf.receiver_data:
      choices.append(name)
    self.add_type = self.AddComboCtrl(2, '', choices=choices, no_edit=True)
    self.add_type.SetSelection(0)
    item = self.AddTextL(3, "and name the new radio")
    self.add_name = self.AddComboCtrl(4, '', choices=["My Radio", "SR with XVtr", "SoftRock"])
    item = self.AddPushButton(5, "Add", self.OnBtnAdd)
    self.NextRow()
    item = self.AddTextL(1, "Rename the radio named")
    self.rename_old = self.AddComboCtrl(2, 'big_radio_name', choices=[], no_edit=True)
    item = self.AddTextL(3, "to the new name")
    self.rename_new = self.AddComboCtrl(4, '', choices=["My Radio", "SR with XVtr", "SoftRock"])
    item = self.AddPushButton(5, "Rename", self.OnBtnRename)
    self.NextRow()
    item = self.AddTextL(1, "Delete the radio named")
    self.delete_name = self.AddComboCtrl(2, 'big_radio_name', choices=[], no_edit=True)
    item = self.AddPushButton(3, "Delete", self.OnBtnDelete)
    self.NextRow()
    self.FitInside()
    self.SetScrollRate(1, 1)
    self.NewRadioNames()
  def SetCurrentRadioText(self):
    radio_dict = local_conf.GetRadioDict(self.radio_name)
    radio_type = radio_dict['hardware_file_type']
    if Settings[1] == "ConfigFileRadio":
      text = 'The current radio is ConfigFileRadio, so all settings come from the config file.  The hardware type is %s.' % radio_type
    else:
      text = "Quisk is running with settings from the radio %s.  The hardware type is %s." % (Settings[1], radio_type)
    self.cur_radio_text.SetLabel(text)
  def DuplicateName(self, name):
    if name in Settings[2] or name == "ConfigFileRadio":
      dlg = wx.MessageDialog(self, "The name already exists.  Please choose a different name.",
          'Quisk', wx.OK)
      dlg.ShowModal()
      dlg.Destroy()
      return True
    return False
  def OnBtnAdd(self, event):
    name = self.add_name.GetValue().strip()
    if not name or self.DuplicateName(name):
      return
    self.add_name.SetValue('')
    typ = self.add_type.GetValue().strip()
    if local_conf.AddRadio(name, typ):
      if Settings[0] != "Ask me":
        Settings[0] = name
      self.NewRadioNames()
      local_conf.settings_changed = True
  def OnBtnRename(self, event):
    old = self.rename_old.GetValue()
    new = self.rename_new.GetValue().strip()
    if not old or not new or self.DuplicateName(new):
      return
    self.rename_new.SetValue('')
    if local_conf.RenameRadio(old, new):
      if old == 'ConfigFileRadio' and Settings[1] == "ConfigFileRadio":
        Settings[1] = new
      elif Settings[1] == old:
        Settings[1] = new
      self.SetCurrentRadioText()
      if Settings[0] != "Ask me":
        Settings[0] = new
      self.NewRadioNames()
      local_conf.settings_changed = True
  def OnBtnDelete(self, event):
    name = self.delete_name.GetValue()
    if not name:
      return
    dlg = wx.MessageDialog(self,
        "Are you sure you want to permanently delete the radio %s?" % name,
        'Quisk', wx.OK|wx.CANCEL|wx.ICON_EXCLAMATION)
    ret = dlg.ShowModal()
    dlg.Destroy()
    if ret == wx.ID_OK and local_conf.DeleteRadio(name):
      self.NewRadioNames()
      local_conf.settings_changed = True
  def OnChoiceStartup(self, ctrl):
    choice = self.start_radio.GetValue()
    if Settings[0] != choice:
      Settings[0] = choice
      local_conf.settings_changed = True
  def NewRadioNames(self):		# Correct all choice lists for changed radio names
    choices = Settings[2][:]			# can rename any available radio
    self.rename_old.SetItems(choices)
    self.rename_old.SetSelection(0)
    if "ConfigFileRadio" in choices:
      choices.remove("ConfigFileRadio")
    if Settings[1] in choices:
      choices.remove(Settings[1])
    self.delete_name.SetItems(choices)	# can not delete ConfigFileRadio nor the current radio
    self.delete_name.SetSelection(0)
    choices = Settings[2] + ["Ask me"]
    if "ConfigFileRadio" not in choices:
      choices.append("ConfigFileRadio")
    self.start_radio.SetItems(choices)	# can start any radio, plus "Ask me" and "ConfigFileRadio"
    try:	# Set text in control
      index = choices.index(Settings[0])	# last used radio, or new or renamed radio
    except:
      num = len(Settings[2])
      if len == 0:
        index = 1
      elif num == 1:
        index = 0
      else:
        index = len(choices) - 2
      Settings[0] = choices[index]
    self.start_radio.SetSelection(index)

class RadioSection(BaseWindow):		# The pages for each section in the second-level notebook for each radio
  def __init__(self, parent, radio_name, section, names):
    BaseWindow.__init__(self, parent)
    self.radio_name = radio_name
    self.names = names
    self.controls_done = False
  def MakeControls(self):
    if self.controls_done:
      return
    self.controls_done = True
    self.num_cols = 8
    #self.MarkCols()
    self.NextRow(3)
    col = 1
    radio_dict = local_conf.GetRadioDict(self.radio_name)
    for name, text, fmt, help_text, values in self.names:
      if name == 'favorites_file_path':
        self.favorites_path = radio_dict.get('favorites_file_path', '')
        row = self.row
        self.row = 1
        item, self.favorites_combo, btn = self.AddTextComboHelp(1, text, self.favorites_path, values, help_text, False, span_text=1, span_combo=4)
        self.favorites_combo.handler = self.OnButtonChangeFavorites
        item = self.AddPushButtonR(7, "Change..", self.OnButtonChangeFavorites, border=0)
        self.row = row
      else:
        if fmt[0:4] in ('dict', 'list'):
          continue
        if name[0:4] == platform_ignore:
          continue
        value = self.GetValue(name, radio_dict)
        no_edit = "choice" in fmt or fmt == 'boolean'
        txt, cb, btn = self.AddTextComboHelp(col, text, value, values, help_text, no_edit)
        cb.handler = self.OnChange
        cb.quisk_data_name = name
        if col == 1:
          col = 4
        else:
          col = 1
          self.NextRow()
    self.AddColSpacer(2, 20)
    self.AddColSpacer(5, 20)
    self.FitInside()
    self.SetScrollRate(1, 1)
  def OnButtonChangeFavorites(self, event):
    if isinstance(event, ComboCtrl):
      path = event.GetValue()
    else:
      direc, fname = os.path.split(getattr(conf, 'favorites_file_in_use'))
      dlg = wx.FileDialog(None, "Choose Favorites File", direc, fname, "*.txt", wx.FD_OPEN)
      if dlg.ShowModal() == wx.ID_OK:
        path = dlg.GetPath()
        self.favorites_combo.SetText(path)
        dlg.Destroy()
      else:
        dlg.Destroy()
        return
    path = path.strip()
    self.favorites_path = path
    local_conf.GetRadioDict(self.radio_name)["favorites_file_path"] = path
    local_conf.settings_changed = True

class RadioHardwareBase(BaseWindow):		# The Hardware page in the second-level notebook for each radio
  def __init__(self, parent, radio_name):
    BaseWindow.__init__(self, parent)
    self.radio_name = radio_name
    self.num_cols = 8
    self.PMcalDialog = None
    #self.MarkCols()
  def AlwaysMakeControls(self):
    radio_dict = local_conf.GetRadioDict(self.radio_name)
    radio_type = radio_dict['hardware_file_type']
    data_names = local_conf.GetReceiverData(radio_type)
    self.AddTextL(1, "These are the hardware settings for a radio of type %s" % radio_type, self.num_cols-1)
    for name, text, fmt, help_text, values in data_names:
      if name == 'hardware_file_name':
        self.hware_path = self.GetValue(name, radio_dict)
        row = self.row
        self.row = 3
        item, self.hware_combo, btn = self.AddTextComboHelp(1, text, self.hware_path, values, help_text, False, span_text=1, span_combo=4)
        self.hware_combo.handler = self.OnButtonChangeHardware
        item = self.AddPushButtonR(7, "Change..", self.OnButtonChangeHardware, border=0)
      elif name == 'widgets_file_name':
        self.widgets_path = self.GetValue(name, radio_dict)
        row = self.row
        self.row = 5
        item, self.widgets_combo, btn = self.AddTextComboHelp(1, text, self.widgets_path, values, help_text, False, span_text=1, span_combo=4)
        self.widgets_combo.handler = self.OnButtonChangeWidgets
        item = self.AddPushButtonR(7, "Change..", self.OnButtonChangeWidgets, border=0)
    self.NextRow(7)
    self.AddColSpacer(2, 20)
    self.AddColSpacer(5, 20)
    self.SetScrollRate(1, 1)
  def OnButtonChangeHardware(self, event):
    if isinstance(event, ComboCtrl):
      path = event.GetValue()
    else:
      direc, fname = os.path.split(self.hware_path)
      dlg = wx.FileDialog(None, "Choose Hardware File", direc, fname, "*.py", wx.FD_OPEN)
      if dlg.ShowModal() == wx.ID_OK:
        path = dlg.GetPath()
        self.hware_combo.SetText(path)
        dlg.Destroy()
      else:
        dlg.Destroy()
        return
    path = path.strip()
    self.hware_path = path
    local_conf.GetRadioDict(self.radio_name)["hardware_file_name"] = path
    local_conf.settings_changed = True
  def OnButtonChangeWidgets(self, event):
    if isinstance(event, ComboCtrl):
      path = event.GetValue()
    else:
      direc, fname = os.path.split(self.widgets_path)
      dlg = wx.FileDialog(None, "Choose Widgets File", direc, fname, "*.py", wx.FD_OPEN)
      if dlg.ShowModal() == wx.ID_OK:
        path = dlg.GetPath()
        self.widgets_combo.SetText(path)
        dlg.Destroy()
      else:
        dlg.Destroy()
        return
    path = path.strip()
    self.widgets_path = path
    local_conf.GetRadioDict(self.radio_name)["widgets_file_name"] = path
    local_conf.settings_changed = True

class RadioHardware(RadioHardwareBase):		# The Hardware page in the second-level notebook for each radio
  def __init__(self, parent, radio_name):
    RadioHardwareBase.__init__(self, parent, radio_name)
    self.AlwaysMakeControls()
    self.HermesBias0 = None
    self.HermesBias1 = None
    radio_dict = local_conf.GetRadioDict(radio_name)
    radio_type = radio_dict['hardware_file_type']
    data_names = local_conf.GetReceiverData(radio_type)
    col = 1
    border = 2
    hermes_board_id = 0
    if radio_type == "Hermes":
      try:
        hermes_board_id = application.Hardware.hermes_board_id
      except:
        pass
    if radio_name == Settings[1] and hasattr(application.Hardware, "ProgramGateware"):
      help_text = "Choose an RBF file and program the Gateware (FPGA software) over Ethernet."
      self.AddTextButtonHelp(1, "Gateware Update", "Program from RBF file..", application.Hardware.ProgramGateware, help_text)
      col = 1
      self.NextRow(self.row + 2)
    for name, text, fmt, help_text, values in data_names:
      if name in ('hardware_file_name', 'widgets_file_name'):
        pass
      elif name[0:4] == platform_ignore:
        pass
      elif name in ('Hermes_BandDictEnTx', 'AlexHPF_TxEn', 'AlexLPF_TxEn'):
        pass
      elif 'Hl2_' in name and hermes_board_id != 6:
        pass
      elif fmt[0:4] in ('dict', 'list'):
        pass
      else:
        if name[0:6] == 'Hware_':		# value comes from the hardware file
          value = application.Hardware.GetValue(name)
        else:
          value = self.GetValue(name, radio_dict)
        no_edit = "choice" in fmt or fmt == 'boolean'
        if name == 'power_meter_calib_name':
          values = self.PowerMeterCalChoices()
          txt, cb, btn = self.AddTextComboHelp(col, text, value, values, help_text, no_edit, border=border)
          cb.handler = self.OnButtonChangePMcal
          self.power_meter_cal_choices = cb
        else:
          txt, cb, btn = self.AddTextComboHelp(col, text, value, values, help_text, no_edit, border=border)
          if name[0:6] == 'Hware_':
            cb.handler = application.Hardware.SetValue
          else:
            cb.handler = self.OnChange
        cb.quisk_data_name = name
        if col == 1:
          col = 4
          border = 0
        else:
          col = 1
          border = 2
          self.NextRow()
    if hermes_board_id == 6:
      if col == 4:
        self.NextRow()
      help_text = ('This controls the bias level for transistors in the final power amplifier.  Enter a level from 0 to 255.'
      '  These changes are temporary.  Press the "Write" button to write the value to the hardware and make it permanent.')
      ## Bias is 0 indexed to match schematic
      txt, self.HermesBias0, btn = self.AddTextSpinnerHelp(1, "Power amp bias 0", 0, 0, 255, help_text)
      txt, self.HermesBias1, btn = self.AddTextSpinnerHelp(4, "Power amp bias 1", 0, 0, 255, help_text)
      enbl = radio_dict["hermes_bias_adjust"] == "True"
      self.HermesBias0.Enable(enbl)
      self.HermesBias1.Enable(enbl)
      self.HermesBias0.Bind(wx.EVT_SPINCTRL, self.OnHermesChangeBias0)
      self.HermesBias1.Bind(wx.EVT_SPINCTRL, self.OnHermesChangeBias1)
      self.HermesWriteBiasButton = self.AddPushButton(7, "Write", self.OnButtonHermesWriteBias, border=0)
      self.HermesWriteBiasButton.Enable(enbl)
    self.FitInside()
  def OnHermesChangeBias0(self, event):
    value = self.HermesBias0.GetValue()
    application.Hardware.ChangeBias0(value)
  def OnHermesChangeBias1(self, event):
    value = self.HermesBias1.GetValue()
    application.Hardware.ChangeBias1(value)
  def OnButtonHermesWriteBias(self, event):
    value0 = self.HermesBias0.GetValue()
    value1 = self.HermesBias1.GetValue()
    application.Hardware.WriteBias(value0, value1)
  def PowerMeterCalChoices(self):
    values = list(conf.power_meter_std_calibrations)		# known calibration names from the config file
    radio_dict = local_conf.GetRadioDict(self.radio_name)
    values += list(radio_dict.get('power_meter_local_calibrations', {}))		# local calibrations
    values.sort()
    values.append('New')
    return values
  def OnButtonChangePMcal(self, ctrl):
    value = ctrl.GetValue()
    name = ctrl.quisk_data_name
    radio_dict = local_conf.GetRadioDict(self.radio_name)
    local_cal = radio_dict.get('power_meter_local_calibrations', {})
    if value == 'New':
      if not self.PMcalDialog:
        self.PMcalDialog = QPowerMeterCalibration(self, list(local_cal))
    else:
      setattr(conf, name, value)
      radio_dict[name] = value
      local_conf.settings_changed = True
      application.Hardware.MakePowerCalibration()
  def ChangePMcalFinished(self, name, table):
    self.PMcalDialog = None
    radio_dict = local_conf.GetRadioDict(self.radio_name)
    local_cal = radio_dict.get('power_meter_local_calibrations', {})
    if name is None:        # Cancel
      name = conf.power_meter_calib_name
      values = self.PowerMeterCalChoices()
    else:
      if table is None:		# delete name
        del local_cal[name]
        name = list(conf.power_meter_std_calibrations)[0]      # replacement name
      else:     # new entry
        local_cal[name] = table
      conf.power_meter_calib_name = name
      radio_dict['power_meter_calib_name'] = name
      radio_dict['power_meter_local_calibrations'] = local_cal
      local_conf.settings_changed = True
      values = self.PowerMeterCalChoices()
      self.power_meter_cal_choices.SetItems(values)
      application.Hardware.MakePowerCalibration()
    try:
      index = values.index(name)
    except:
      index = 0
    self.power_meter_cal_choices.SetSelection(index)

class RadioHardwareSoapySDR(RadioHardwareBase):	# The Hardware page in the second-level notebook for the SoapySDR radios
  name_text = {
'soapy_gain_mode_rx' : 'Rx gain mode',
'soapy_setAntenna_rx' : 'Rx antenna name',
'soapy_setBandwidth_rx' : 'Rx bandwidth kHz',
'soapy_setSampleRate_rx' : 'Rx sample rate kHz',
'soapy_device' : 'Device name',
'soapy_gain_mode_tx' : 'Tx gain mode',
'soapy_setAntenna_tx' : 'Tx antenna name',
'soapy_setBandwidth_tx' : 'Tx bandwidth kHz',
'soapy_setSampleRate_tx' : 'Tx sample rate kHz',
}

  help_text = {
'soapy_gain_mode_rx' : 'Choose "total" to set the total gain, "detailed" to set multiple gain elements individually, \
or "automatic" for automatic gain control. The "detailed" or "automatic" may not be available depending on your hardware.',

'soapy_setAntenna_rx' : 'Choose the antenna to use for receive.',

'soapy_device' : "SoapySDR provides an interface to various radio hardware. The device name specifies \
the hardware device. Create a new radio for each hardware you have. Changing the device \
name requires re-entering all the hardware settings because different hardware has \
different settings. Also, the hardware device must be turned on when you change the \
device name so that Quisk can read the available settings.",

'soapy_gain_mode_tx' : 'Choose "total" to set the total gain, "detailed" to set multiple gain elements individually, \
or "automatic" for automatic gain control. The "detailed" or "automatic" may not be available depending on your hardware.',

'soapy_setAntenna_tx' : 'Choose the antenna to use for transmit.',

}
  def __init__(self, parent, radio_name):
    RadioHardwareBase.__init__(self, parent, radio_name)
    self.no_device = "No device specified"
    if soapy:
      self.AlwaysMakeControls()
      self.MakeSoapyControls()
    else:
      radio_dict = local_conf.GetRadioDict(self.radio_name)
      radio_type = radio_dict['hardware_file_type']
      self.AddTextL(1, "These are the hardware settings for a radio of type %s" % radio_type, self.num_cols-1)
      self.NextRow()
      self.AddTextL(1, "The shared library from the SoapySDR project is not available.")
      self.NextRow()
      self.AddTextL(1, "The shared library is not installed or is not compatible (perhaps 32 versus 64 bit versions).")
      self.NextRow()
      return
    #self.MarkCols()
  def NextCol(self):
    if self.col == 1:
      self.col = 4
      self.border = 0
    else:
      self.col = 1
      self.border = 2
      self.NextRow()
  def MakeSoapyControls(self):
    self.gains_rx = []
    self.gains_tx = []
    radio_dict = local_conf.GetRadioDict(self.radio_name)
    local_conf.InitSoapyNames(radio_dict)
    self.border = 2
    name = 'soapy_device'
    device = radio_dict.get(name, self.no_device)
    txt, self.edit_soapy_device, btn = self.AddTextEditHelp(1, self.name_text[name], device, self.help_text[name], span1=1, span2=4)
    self.AddPushButtonR(7, "Change..", self.OnButtonChangeSoapyDevice, border=0)
    self.NextRow()
    self.NextRow()
    self.col = 1
    if device == self.no_device:
      self.FitInside()
      return

    if radio_dict.get("soapy_file_version", 0) < soapy_software_version:
      text = "Please re-enter the device name. This will read additional parameters from the hardware."
      self.AddTextL(self.col, text, span=6)
      self.FitInside()
      return

    # Receive parameters
    name = 'soapy_setSampleRate_rx'
    help_text = 'Available sample rates: '
    rates = ['48', '50', '240', '250', '960', '1000']
    for dmin, dmax, dstep in radio_dict.get('soapy_getSampleRateRange_rx', ()):
      tmin = FormatKhz(dmin * 1E-3)
      if tmin not in rates:
        rates.append(tmin)
      if abs(dmin - dmax) < 0.5:
        help_text = help_text + '%s; ' % tmin
      elif dstep < 0.5:
        help_text = help_text + '%s to %s; ' % (tmin, FormatKhz(dmax * 1E-3))
      else:
        help_text = help_text + '%s to %s by %s; ' % (tmin, FormatKhz(dmax * 1E-3), FormatKhz(dstep * 1E-3))
    help_text = help_text[0:-2] + '.'
    if rates:
      rates.sort(key=SortKey)
      rate = radio_dict.get(name, '')
      txt, cb, btn = self.AddTextComboHelp(self.col, self.name_text[name], rate, rates, help_text, False, border=self.border)
      cb.handler = self.OnChange
      cb.quisk_data_name = name
      self.NextCol()

    len_gain_names = len(radio_dict.get('soapy_listGainsValues_rx', ()))
    name = 'soapy_gain_mode_rx'
    gain_mode = radio_dict[name]
    choices = ['total']
    if len_gain_names >= 3:
      choices.append('detailed')
    if radio_dict.get('soapy_hasGainMode_rx', 0):
      choices.append('automatic')
    if gain_mode not in choices:
      gain_mode = radio_dict[name] = 'total'
      local_conf.settings_changed = True
    txt, cb, btn = self.AddTextComboHelp(self.col, self.name_text[name], gain_mode, choices, self.help_text[name], True, border=self.border)
    cb.handler = self.OnChange
    cb.quisk_data_name = name
    self.NextCol()

    name = 'soapy_gain_values_rx'
    values = radio_dict[name]
    for name2, dmin, dmax, dstep in radio_dict.get('soapy_listGainsValues_rx', ()):
      if dstep < 1E-4:
        dstep = 0.5
      text = "Rx gain %s" % name2
      help_text = 'Rf gain min %f, max %f, step %f' % (dmin, dmax, dstep)
      value = values.get(name2, '0')
      value = float(value)
      txt, spn, btn = self.AddTextDblSpinnerHelp(self.col, text, value, dmin, dmax, dstep, help_text, border=self.border)
      spn.quisk_data_name = name
      spn.quisk_data_name2 = name2
      spn.Bind(wx.EVT_SPINCTRLDOUBLE, self.OnGain)
      self.gains_rx.append(spn)
      self.NextCol()
      if len_gain_names < 3:	# for 1 or 2 names, just show total gain item
        break
    self.FixGainButtons('soapy_gain_mode_rx')

    name = 'soapy_setAntenna_rx'
    antenna = radio_dict[name]
    antennas = radio_dict.get('soapy_listAntennas_rx', ())
    if antenna not in antennas:
      if antennas:
        antenna = antennas[0]
      else:
        antenna = ''
      radio_dict[name] = antenna
      local_conf.settings_changed = True
    if antennas:
      txt, cb, btn = self.AddTextComboHelp(self.col, self.name_text[name], antenna, antennas, self.help_text[name], True, border=self.border)
      cb.handler = self.OnChange
      cb.quisk_data_name = name
      self.NextCol()

    name = 'soapy_setBandwidth_rx'
    help_text = 'Available bandwidth: '
    bandwidths = []
    for dmin, dmax, dstep in radio_dict.get('soapy_getBandwidthRange_rx', ()):
      tmin = FormatKhz(dmin * 1E-3)
      bandwidths.append(tmin)
      if abs(dmin - dmax) < 0.5:
        help_text = help_text + '%s; ' % tmin
      elif dstep < 0.5:
        help_text = help_text + '%s to %s; ' % (tmin, FormatKhz(dmax * 1E-3))
      else:
        help_text = help_text + '%s to %s by %s; ' % (tmin, FormatKhz(dmax * 1E-3), FormatKhz(dstep * 1E-3))
    help_text = help_text[0:-2] + '.'
    if bandwidths:
      bandwidth = radio_dict.get(name, '')
      txt, cb, btn = self.AddTextComboHelp(self.col, self.name_text[name], bandwidth, bandwidths, help_text, False, border=self.border)
      cb.handler = self.OnChange
      cb.quisk_data_name = name
      self.NextCol()

    # Transmit parameters
    if self.col != 1:
      self.NextCol()
    name = 'soapy_enable_tx'
    enable = radio_dict.get(name, 'Disable')
    help_text = 'This will enable or disable the transmit function. If changed, you must restart Quisk.'
    txt, cb, btn = self.AddTextComboHelp(self.col, 'Tx enable', enable, ['Enable', 'Disable'], help_text, True, border=self.border)
    cb.handler = self.OnChange
    cb.quisk_data_name = name
    self.NextCol()

    name = 'soapy_setSampleRate_tx'
    help_text = 'Available sample rates: '
    rates = []
    for dmin, dmax, dstep in radio_dict.get('soapy_getSampleRateRange_tx', ()):
      tmin = FormatKhz(dmin * 1E-3)
      rates.append(tmin)
      if abs(dmin - dmax) < 0.5:
        help_text = help_text + '%s; ' % tmin
      elif dstep < 0.5:
        help_text = help_text + '%s to %s; ' % (tmin, FormatKhz(dmax * 1E-3))
      else:
        help_text = help_text + '%s to %s by %s; ' % (tmin, FormatKhz(dmax * 1E-3), FormatKhz(dstep * 1E-3))
    help_text = help_text[0:-2] + '.'
    if rates:
      rate = radio_dict.get(name, '')
      rates = ('48', '50', '96', '100', '192')
      txt, cb, btn = self.AddTextComboHelp(self.col, self.name_text[name], rate, rates, help_text, True, border=self.border)
      cb.handler = self.OnChange
      cb.quisk_data_name = name
      self.NextCol()

    len_gain_names = len(radio_dict.get('soapy_listGainsValues_tx', ()))
    name = 'soapy_gain_mode_tx'
    gain_mode = radio_dict[name]
    choices = ['total']
    if len_gain_names >= 3:
      choices.append('detailed')
    if radio_dict.get('soapy_hasGainMode_tx', 0):
      choices.append('automatic')
    if gain_mode not in choices:
      gain_mode = radio_dict[name] = 'total'
      local_conf.settings_changed = True
    txt, cb, btn = self.AddTextComboHelp(self.col, self.name_text[name], gain_mode, choices, self.help_text[name], True, border=self.border)
    cb.handler = self.OnChange
    cb.quisk_data_name = name
    self.NextCol()

    name = 'soapy_gain_values_tx'
    values = radio_dict[name]
    for name2, dmin, dmax, dstep in radio_dict.get('soapy_listGainsValues_tx', ()):
      if dstep < 1E-4:
        dstep = 0.5
      text = "Tx gain %s" % name2
      help_text = 'Rf gain min %f, max %f, step %f' % (dmin, dmax, dstep)
      value = values.get(name2, '0')
      value = float(value)
      txt, spn, btn = self.AddTextDblSpinnerHelp(self.col, text, value, dmin, dmax, dstep, help_text, border=self.border)
      spn.quisk_data_name = name
      spn.quisk_data_name2 = name2
      spn.Bind(wx.EVT_SPINCTRLDOUBLE, self.OnGain)
      self.gains_tx.append(spn)
      self.NextCol()
      if len_gain_names < 3:	# for 1 or 2 names, just show total gain item
        break
    self.FixGainButtons('soapy_gain_mode_tx')

    name = 'soapy_setAntenna_tx'
    antenna = radio_dict[name]
    antennas = radio_dict.get('soapy_listAntennas_tx', ())
    if antenna not in antennas:
      if antennas:
        antenna = antennas[0]
      else:
        antenna = ''
      radio_dict[name] = antenna
      local_conf.settings_changed = True
    if antennas:
      txt, cb, btn = self.AddTextComboHelp(self.col, self.name_text[name], antenna, antennas, self.help_text[name], True, border=self.border)
      cb.handler = self.OnChange
      cb.quisk_data_name = name
      self.NextCol()

    name = 'soapy_setBandwidth_tx'
    help_text = 'Available bandwidths: '
    bandwidths = []
    for dmin, dmax, dstep in radio_dict.get('soapy_getBandwidthRange_tx', ()):
      tmin = FormatKhz(dmin * 1E-3)
      bandwidths.append(tmin)
      if abs(dmin - dmax) < 0.5:
        help_text = help_text + '%s; ' % tmin
      elif dstep < 0.5:
        help_text = help_text + '%s to %s; ' % (tmin, FormatKhz(dmax * 1E-3))
      else:
        help_text = help_text + '%s to %s by %s; ' % (tmin, FormatKhz(dmax * 1E-3), FormatKhz(dstep * 1E-3))
    help_text = help_text[0:-2] + '.'
    if bandwidths:
      bandwidth = radio_dict.get(name, '')
      txt, cb, btn = self.AddTextComboHelp(self.col, self.name_text[name], bandwidth, bandwidths, help_text, False, border=self.border)
      cb.handler = self.OnChange
      cb.quisk_data_name = name
      self.NextCol()

    self.FitInside()
  def FixGainButtons(self, name):
    radio_dict = local_conf.GetRadioDict(self.radio_name)
    gain_mode = radio_dict[name]
    if name[-3:] == '_tx':
      controls = self.gains_tx
    else:
      controls = self.gains_rx
    for i in range(len(controls)):
      ctrl = controls[i]
      if gain_mode == "automatic":
        ctrl.Enable(False)
      elif gain_mode == "total":
        if i == 0:
          ctrl.Enable(True)
        else:
          ctrl.Enable(False)
      else:	# gain_mode is "detailed"
        if i == 0:
          ctrl.Enable(False)
        else:
          ctrl.Enable(True)
  def OnButtonChangeSoapyDevice(self, event):
    if not soapy:
      txt = "Soapy shared library (DLL) is not available."
      msg = wx.MessageDialog(None, txt, 'SoapySDR Error', wx.OK|wx.ICON_ERROR)
      msg.ShowModal()
      msg.Destroy()
      return
    try:
      choices = self.GetSoapyDevices()
    except:
      #traceback.print_exc()
      choices = []
    if not choices:
      choices = ['No devices were found.']
    device = self.edit_soapy_device.GetValue()
    width = application.main_frame.GetSize().width
    width = width * 50 // 100
    parent = self.edit_soapy_device.GetParent()
    dlg = ListEditDialog(parent, "Change Soapy Device", device, choices, width)
    ok = dlg.ShowModal()
    if ok != wx.ID_OK:
      dlg.Destroy()
      return
    device = dlg.GetValue()
    dlg.Destroy()
    if device == self.no_device:
      return
    if Settings[1] == self.radio_name:
      txt = "Changing the active radio requires a shutdown and restart. Proceed?"
      msg = wx.MessageDialog(None, txt, 'SoapySDR Change to Active Radio', wx.OK|wx.CANCEL|wx.ICON_INFORMATION)
      ok = msg.ShowModal()
      msg.Destroy()
      if ok == wx.ID_OK:
        soapy.close_device(1)
      else:
        return
    txt = soapy.open_device(device, 0, 0)
    if txt[0:8] == 'Capture ':
      radio_dict = local_conf.GetRadioDict(self.radio_name)
      radio_dict['soapy_device'] = device
      radio_dict['soapy_file_version'] = soapy_software_version
      self.edit_soapy_device.ChangeValue(device)
      # Record the new SoapySDR parameters for the new device. Do not change the old data values yet.
      for name in ('soapy_listAntennas_rx', 'soapy_hasGainMode_rx', 'soapy_listGainsValues_rx',
                   'soapy_listAntennas_tx', 'soapy_hasGainMode_tx', 'soapy_listGainsValues_tx',
	           'soapy_getFullDuplex_rx', 'soapy_getSampleRateRange_rx', 'soapy_getSampleRateRange_tx',
                   'soapy_getBandwidthRange_rx', 'soapy_getBandwidthRange_tx',
                  ):
        radio_dict[name] = soapy.get_parameter(name, 0)
      soapy.close_device(0)
      local_conf.settings_changed = True
      # Clear our sizer and re-create all the controls
      self.gbs.Clear(True)
      self.gbs.Add((self.charx, self.charx), (0, 0))
      self.row = 1
      RadioHardwareBase.AlwaysMakeControls(self)
      self.MakeSoapyControls()
      txt = "Please check the settings for the new hardware device."
      msg = wx.MessageDialog(None, txt, 'SoapySDR Change to Radio', wx.OK|wx.ICON_INFORMATION)
      msg.ShowModal()
      msg.Destroy()
    else:
      msg = wx.MessageDialog(None, txt, 'SoapySDR Device Error', wx.OK|wx.ICON_ERROR)
      msg.ShowModal()
      msg.Destroy()
  def GetSoapyDevices(self):
    choices = []
    for dct in soapy.get_device_list():
      text = ''
      try:
        driver = dct["driver"]
      except:
        pass
      else:
        text = 'driver=%s' % driver
      try:
        label = dct["label"]
      except:
        pass
      else:
        text = text + ', label=%s' % label
      choices.append(text)
    return choices
  def OnChange(self, ctrl):
    name = ctrl.quisk_data_name
    value = ctrl.GetValue()
    radio_dict = local_conf.GetRadioDict(self.radio_name)
    radio_dict[name] = value
    local_conf.settings_changed = True
    # Immediate changes
    if name in ('soapy_gain_mode_rx', 'soapy_gain_mode_tx'):
      self.FixGainButtons(name)
    if soapy and self.radio_name == Settings[1]:	# changed for current radio
      application.Hardware.ImmediateChange(name, value)
  def OnGain(self, event):
    radio_dict = local_conf.GetRadioDict(self.radio_name)
    obj = event.GetEventObject()
    value = obj.GetValue()
    name = obj.quisk_data_name
    radio_dict[name][obj.quisk_data_name2] = value
    local_conf.settings_changed = True
    if soapy and self.radio_name == Settings[1]:	# changed for current radio
      application.Hardware.ChangeGain(name[-3:])

class RadioSound(BaseWindow):		# The Sound page in the second-level notebook for each radio
  """Configure the available sound devices."""
  sound_names = (		# same order as grid labels
    ('playback_rate', '', '', '', 'name_of_sound_play'),
    ('mic_sample_rate', 'mic_channel_I', 'mic_channel_Q', '', 'microphone_name'),
    ('sample_rate', 'channel_i', 'channel_q', 'channel_delay', 'name_of_sound_capt'),
    ('mic_playback_rate', 'mic_play_chan_I', 'mic_play_chan_Q', 'tx_channel_delay', 'name_of_mic_play'),
    ('', '', '', '', 'digital_input_name'),
    ('', '', '', '', 'digital_output_name'),
    ('', '', '', '', 'sample_playback_name'),
    ('', '', '', '', 'digital_rx1_name'),
    )
  def __init__(self, parent, radio_name):
    BaseWindow.__init__(self, parent)
    self.radio_name = radio_name
    self.controls_done = False
  def MakeControls(self):
    if self.controls_done:
      return
    self.controls_done = True
    self.radio_dict = local_conf.GetRadioDict(self.radio_name)
    self.num_cols = 8
    thename = platform_accept + "latency_millisecs"
    for name, text, fmt, help_text, values in local_conf.GetSectionData('Sound'):
      if name == thename:
        value = self.GetValue(name, self.radio_dict)
        no_edit = "choice" in fmt or fmt == 'boolean'
        txt, cb, btn = self.AddTextComboHelp(1, text, value, values, help_text, no_edit)
        cb.handler = self.OnChange
        cb.quisk_data_name = name
        break
    for name, text, fmt, help_text, values in local_conf.GetSectionData('Sound'):
      if name == 'digital_output_level':
        value = self.GetValue(name, self.radio_dict)
        no_edit = "choice" in fmt or fmt == 'boolean'
        txt, cb, btn = self.AddTextComboHelp(4, text, value, values, help_text, no_edit)
        cb.handler = self.OnChange
        cb.quisk_data_name = name
        break
    self.NextRow()
    # Add the grid for the sound settings
    sizer = wx.GridBagSizer(2, 2)
    sizer.SetEmptyCellSize((self.charx, self.charx))
    self.gbs.Add(sizer, (self.row, 0), span=(1, self.num_cols))
    gbs = self.gbs
    self.gbs = sizer
    self.row = 1
    dev_capt, dev_play = QS.sound_devices()
    if sys.platform != 'win32':
      for i in range(len(dev_capt)):
        dev_capt[i] = "alsa:" + dev_capt[i]
      for i in range(len(dev_play)):
        dev_play[i] = "alsa:" + dev_play[i]
      show = self.GetValue('show_pulse_audio_devices', self.radio_dict)
      if show == 'True':
        dev_capt.append("pulse # Use the default pulse device")
        dev_play.append("pulse # Use the default pulse device")
        for n0, n1, n2 in application.pa_dev_capt:
          dev_capt.append("pulse:%s" % n0)
        for n0, n1, n2 in application.pa_dev_play:
          dev_play.append("pulse:%s" % n0)
    dev_capt.insert(0, '')
    dev_play.insert(0, '')
    self.AddTextC(1, "Stream")
    self.AddTextCHelp(2, "Rate",
"This is the sample rate for the device in Hertz." "Some devices have fixed rates that can not be changed.")
    self.AddTextCHelp(3, "Ch I", "This is the in-phase channel for devices with I/Q data, and the main channel for other devices.")
    self.AddTextCHelp(4, "Ch Q", "This is the quadrature channel for devices with I/Q data, and the second channel for other devices.")
    self.AddTextCHelp(5, "Delay", "Some older devices have a one sample channel delay between channels.  "
"This must be corrected for devices with I/Q data.  Enter the channel number to delay; either the I or Q channel number.  "
"For no delay, leave this blank.")
    self.AddTextCHelp(6, "Sound Device", "This is the name of the sound device.  For Windows, this is the DirectX name.  "
"For Linux you can use the Alsa device, the PortAudio device or the PulseAudio device.  "
"The Alsa device are recommended because they have lower latency.  See the documentation for more information.")
    self.NextRow()
    label_help = (
      (1, "Radio Sound Output", "This is the radio sound going to the headphones or speakers."),
      (0, "Microphone Input", "This is the monophonic microphone source.  Set the channel if the source is stereo."),
      (0, "I/Q Rx Sample Input", "This is the sample source if it comes from a sound device, such as a SoftRock."),
      (1, "I/Q Tx Sample Output", "This is the transmit sample audio sent to a SoftRock."),
      (0, "External Digital Input", "This is the loopback sound device for Rx samples received from a digital program such as FlDigi."),
      (1, "External Digital Output", "This is the loopback sound device for Tx samples sent to a digital program such as FlDigi."),
      (1, "Raw Digital Output", "This sends the received I/Q data to another program."),
      (1, "Digital Rx1 Output", "This sends sub-receiver 1 output to another program."),
    )
    choices = (("48000", "96000", "192000"), ("0", "1"), ("0", "1"), (" ", "0", "1"))
    r = 0
    if "SoftRock" in self.radio_dict['hardware_file_type']:		# Samples come from sound card
      softrock = True
    else:
      softrock = False
    for is_output, label, helptxt in label_help:
      self.AddTextLHelp(1, label, helptxt)
      # Add col 0
      value = self.ItemValue(r, 0)
      if value is None:
        value = ''
      data_name = self.sound_names[r][0]
      if r == 0:
        cb = self.AddComboCtrl(2, value, choices=("48000", "96000", "192000"), right=True)
      if r == 1:
        cb = self.AddComboCtrl(2, value, choices=("48000", "8000"), right=True, no_edit=True)
      if softrock:
        if r == 2:
          cb = self.AddComboCtrl(2, value, choices=("48000", "96000", "192000"), right=True)
        if r == 3:
          cb = self.AddComboCtrl(2, value, choices=("48000", "96000", "192000"), right=True)
      else:
        if r == 2:
          cb = self.AddComboCtrl(2, '', choices=("",), right=True)
          cb.Enable(False)
        if r == 3:
          cb = self.AddComboCtrl(2, '', choices=("",), right=True)
          cb.Enable(False)
      if r == 4:
        cb = self.AddComboCtrl(2, "48000", choices=("48000",), right=True, no_edit=True)
        cb.Enable(False)
      if r == 5:
        cb = self.AddComboCtrl(2, "48000", choices=("48000",), right=True, no_edit=True)
        cb.Enable(False)
      if r == 6:
        cb = self.AddComboCtrl(2, "48000", choices=("48000",), right=True, no_edit=True)
        cb.Enable(False)
      if r == 7:
        cb = self.AddComboCtrl(2, "48000", choices=("48000",), right=True, no_edit=True)
        cb.Enable(False)
      cb.handler = self.OnChange
      cb.quisk_data_name = data_name
      # Add col 1, 2, 3
      for col in range(1, 4):
        value = self.ItemValue(r, col)
        data_name = self.sound_names[r][col]
        if value is None:
          cb = self.AddComboCtrl(col + 2, ' ', choices=[], right=True)
          cb.Enable(False)
        else:
          cb = self.AddComboCtrl(col + 2, value, choices=choices[col], right=True)
        cb.handler = self.OnChange
        cb.quisk_data_name = self.sound_names[r][col]
      # Add col 4
      if not softrock and r in (2, 3):
        cb = self.AddComboCtrl(6, '', choices=[''])
        cb.Enable(False)
      elif is_output:
        cb = self.AddComboCtrl(6, self.ItemValue(r, 4), choices=dev_play)
      else:
        cb = self.AddComboCtrl(6, self.ItemValue(r, 4), choices=dev_capt)
      cb.handler = self.OnChange
      cb.quisk_data_name = platform_accept + self.sound_names[r][4]
      self.NextRow()
      r += 1
    self.gbs = gbs
    self.FitInside()
    self.SetScrollRate(1, 1)
  def ItemValue(self, row, col):
    data_name = self.sound_names[row][col]
    if col == 4:		# Device names
      data_name = platform_accept + data_name
      value = self.GetValue(data_name, self.radio_dict)
      return value
    elif data_name:
      value = self.GetValue(data_name, self.radio_dict)
      if col == 3:		# Delay
        if value == "-1":
          value = ''
      return value
    return None
  def OnChange(self, ctrl):
    data_name = ctrl.quisk_data_name
    value = ctrl.GetValue()
    if data_name in ('channel_delay', 'tx_channel_delay'):
      value = value.strip()
      if not value:
        value = "-1"
    self.OnChange2(ctrl, value)

class RadioBands(BaseWindow):		# The Bands page in the second-level notebook for each radio
  def __init__(self, parent, radio_name):
    BaseWindow.__init__(self, parent)
    self.radio_name = radio_name
    self.controls_done = False
  def MakeControls(self):
    if self.controls_done:
      return
    self.controls_done = True
    radio_dict = local_conf.GetRadioDict(self.radio_name)
    radio_type = radio_dict['hardware_file_type']
    self.num_cols = 8
    #self.MarkCols()
    self.NextRow()
    self.AddTextCHelp(1, "Bands",
"This is a list of the bands that Quisk understands.  A check mark means that the band button is displayed.  A maximum of "
"14 bands may be displayed.")
    self.AddTextCHelp(2, "    Start MHz",
"This is the start of the band in megahertz.")
    self.AddTextCHelp(3, "    End MHz",
"This is the end of the band in megahertz.")
    heading_row = self.row
    self.NextRow()
    band_labels = radio_dict['bandLabels'][:]
    for i in range(len(band_labels)):
      if isinstance(band_labels[i], (list, tuple)):
        band_labels[i] = band_labels[i][0]
    band_edge = radio_dict['BandEdge']
    # band_list is a list of all known bands
    band_list = local_conf.originalBandEdge
    band_list = list(band_list)
    band_list.sort(key=self.SortCmp)
    band_list.append('Time')
    if local_conf.ReceiverHasName(radio_type, 'tx_level'):
      tx_level = self.GetValue('tx_level', radio_dict)
      radio_dict['tx_level'] = tx_level     # Make sure the dictionary is in radio_dict
    else:
      tx_level = None
    try:
      transverter_offset = radio_dict['bandTransverterOffset']
    except:
      transverter_offset = {}
      radio_dict['bandTransverterOffset'] = transverter_offset     # Make sure the dictionary is in radio_dict
    try:
      hiqsdr_bus = radio_dict['HiQSDR_BandDict']
    except:
      hiqsdr_bus = None
    try:
      hermes_bus = radio_dict['Hermes_BandDict']
    except:
      hermes_bus = None
    self.band_checks = []
    # Add the Audio band.  This must be first to allow for column labels.
    cb = self.AddCheckBox(1, 'Audio', self.OnChangeBands)
    self.band_checks.append(cb)
    if 'Audio' in band_labels:
      cb.SetValue(True)
    self.NextRow()
    start_row = self.row
    # Add check box, start, end
    for band in band_list:
      cb = self.AddCheckBox(1, band, self.OnChangeBands)
      self.band_checks.append(cb)
      if band in band_labels:
        cb.SetValue(True)
      try:
        start, end = band_edge[band]
        start = str(start * 1E-6)
        end = str(end * 1E-6)
      except:
        try:
          start, end = local_conf.originalBandEdge[band]
          start = str(start * 1E-6)
          end = str(end * 1E-6)
        except:
          start = ''
          end = ''
      cb = self.AddComboCtrl(2, start, choices=(start, ), right=True)
      cb.handler = self.OnChangeBandStart
      cb.quisk_band = band
      cb = self.AddComboCtrl(3, end, choices=(end, ), right=True)
      cb.handler = self.OnChangeBandEnd
      cb.quisk_band = band
      self.NextRow()
    col = 3
    # Add tx_level
    if tx_level is not None:
      col += 1
      self.row = heading_row
      self.AddTextCHelp(col, "    Tx Level",
"This is the transmit level for each band.  The level is a number from zero to 255.  Changes are immediate.")
      self.row = start_row
      for band in band_list:
        try:
          level = tx_level[band]
          level = str(level)
        except:
          try:
            level = tx_level[None]
            tx_level[band] = level      # Fill in tx_level for each band
            level = str(level)
          except:
            tx_level[band] = 0
            level = '0'
        cb = self.AddComboCtrl(col, level, choices=(level, ), right=True)
        cb.handler = self.OnChangeDict
        cb.quisk_data_name = 'tx_level'
        cb.quisk_band = band
        self.NextRow()
    # Add transverter offset
    if isinstance(transverter_offset, dict):
      col += 1
      self.row = heading_row
      self.AddTextCHelp(col, "    Transverter Offset",
"If you use a transverter, you need to tune your hardware to a frequency lower than\
 the frequency displayed by Quisk.  For example, if you have a 2 meter transverter,\
 you may need to tune your hardware from 28 to 30 MHz to receive 144 to 146 MHz.\
 Enter the transverter offset in Hertz.  For this to work, your\
 hardware must support it.  Currently, the HiQSDR, SDR-IQ and SoftRock are supported.")
      self.row = start_row
      for band in band_list:
        try:
          offset = transverter_offset[band]
        except:
          offset = ''
        else:
          offset = str(offset)
        cb = self.AddComboCtrl(col, offset, choices=(offset, ), right=True)
        cb.handler = self.OnChangeDictBlank
        cb.quisk_data_name = 'bandTransverterOffset'
        cb.quisk_band = band
        self.NextRow()
    # Add hiqsdr_bus
    if hiqsdr_bus is not None:
      bus_text = 'The IO bus is used to select filters for each band.  Refer to the documentation for your filter board to see what number to enter.'
      col += 1
      self.row = heading_row
      self.AddTextCHelp(col, "    IO Bus", bus_text)
      self.row = start_row
      for band in band_list:
        try:
          bus = hiqsdr_bus[band]
        except:
          bus = ''
          bus_choice = ('11', )
        else:
          bus = str(bus)
          bus_choice = (bus, )
        cb = self.AddComboCtrl(col, bus, bus_choice, right=True)
        cb.handler = self.OnChangeDict
        cb.quisk_data_name = 'HiQSDR_BandDict'
        cb.quisk_band = band
        self.NextRow()
    # Add hermes_bus
    if hermes_bus is not None:
      bus_text = 'The IO bus is used to select filters for each band.  Check the bit for a "1", and uncheck the bit for a "0".\
  Bits are shown in binary number order.  For example, decimal 9 is 0b1001, so check bits 3 and 0.\
  Changes are immediate (no need to restart).\
  Refer to the documentation for your filter board to see which bits to set.\
  The Rx bits are used for both receive and transmit, unless the "Enable" box is checked.\
  Then you can specify different filters for Rx and Tx.\
  If multiple receivers are in use, the Rx filter will be that of the highest frequency band.'
      col += 1
      self.row = heading_row
      self.AddTextCHelp(col, " Rx IO Bus", bus_text)
      self.AddTextCHelp(col + 1, " Tx IO Bus", bus_text)
      self.row += 1
      self.AddTextC(col, "6...Bits...0")
      btn = self.AddCheckBox(col + 1, "  Enable", self.ChangeIOTxEnable, flag=wx.ALIGN_CENTER|wx.ALIGN_CENTER_VERTICAL)
      value = self.GetValue("Hermes_BandDictEnTx", radio_dict)
      value = value == 'True'
      btn.SetValue(value)
      self.row = start_row
      try:
        hermes_tx_bus = radio_dict['Hermes_BandDictTx']
      except:
        hermes_tx_bus = {}
      for band in band_list:
        try:
          bus = int(hermes_bus[band])
        except:
          bus = 0
        self.AddBitField(col, 7, 'Hermes_BandDict', band, bus, self.ChangeIO)
        try:
          bus = int(hermes_tx_bus[band])
        except:
          bus = 0
        self.AddBitField(col + 1, 7, 'Hermes_BandDictTx', band, bus, self.ChangeIO)
        self.NextRow()
    self.FitInside()
    self.SetScrollRate(1, 1)
  def SortCmp(self, item1):
    # Numerical conversion to  megahertz
    try:
      if item1[-2:] == 'cm':
        item1 = float(item1[0:-2]) * .01
        item1 = 300.0 / item1
      elif item1[-1] == 'k':
        item1 = float(item1[0:-1]) * .001
      else:
        item1 = float(item1)
        item1 = 300.0 / item1
    except:
      item1 = 50000.0
    return item1
  def OnChangeBands(self, ctrl):
    band_list = []
    count = 0
    for cb in self.band_checks:
      if cb.IsChecked():
        band = cb.GetLabel()
        count += 1
        if band == '60' and len(conf.freq60) > 1:
          band_list.append(('60', ) * len(conf.freq60))
        elif band == 'Time' and len(conf.bandTime) > 1:
          band_list.append(('Time', ) * len(conf.bandTime))
        else:
          band_list.append(band)
    if count > 14:
      dlg = wx.MessageDialog(None,
        "There are more than the maximum of 14 bands checked.  Please remove some checks.",
        'List of Bands', wx.OK|wx.ICON_ERROR)
      dlg.ShowModal()
      dlg.Destroy()
    else:
      radio_dict = local_conf.GetRadioDict(self.radio_name)
      radio_dict['bandLabels'] = band_list
      local_conf.settings_changed = True
  def OnChangeBandStart(self, ctrl):
    radio_dict = local_conf.GetRadioDict(self.radio_name)
    band_edge = radio_dict['BandEdge']
    band = ctrl.quisk_band
    start, end = band_edge.get(band, (0, 9999))
    value = ctrl.GetValue()
    if self.FormatOK(value, 'numb'):
      start = int(float(value) * 1E6 + 0.1)
      band_edge[band] = (start, end)
      local_conf.settings_changed = True
  def OnChangeBandEnd(self, ctrl):
    radio_dict = local_conf.GetRadioDict(self.radio_name)
    band_edge = radio_dict['BandEdge']
    band = ctrl.quisk_band
    start, end = band_edge.get(band, (0, 9999))
    value = ctrl.GetValue()
    if self.FormatOK(value, 'numb'):
      end = int(float(value) * 1E6 + 0.1)
      band_edge[band] = (start, end)
      local_conf.settings_changed = True
  def OnChangeDict(self, ctrl):
    radio_dict = local_conf.GetRadioDict(self.radio_name)
    dct = radio_dict[ctrl.quisk_data_name]
    band = ctrl.quisk_band
    value = ctrl.GetValue()
    if self.FormatOK(value, 'inte'):
      value = int(value)
      dct[band] = value
      local_conf.settings_changed = True
      if ctrl.quisk_data_name == 'tx_level' and hasattr(application.Hardware, "SetTxLevel"):
        application.Hardware.SetTxLevel()
  def OnChangeDictBlank(self, ctrl):
    radio_dict = local_conf.GetRadioDict(self.radio_name)
    dct = radio_dict[ctrl.quisk_data_name]
    band = ctrl.quisk_band
    value = ctrl.GetValue()
    value = value.strip()
    if not value:
      if band in dct:
        del dct[band]
        local_conf.settings_changed = True
    elif self.FormatOK(value, 'inte'):
      value = int(value)
      dct[band] = value
      local_conf.settings_changed = True
  def ChangeIO(self, control):
    radio_dict = local_conf.GetRadioDict(self.radio_name)
    dct = radio_dict[control.quisk_data_name]
    band = control.quisk_band
    dct[band] = control.value
    local_conf.settings_changed = True
    if hasattr(application.Hardware, "ChangeBandFilters"):
      application.Hardware.ChangeBandFilters()
  def ChangeIOTxEnable(self, event):
    name = "Hermes_BandDictEnTx"
    radio_dict = local_conf.GetRadioDict(self.radio_name)
    if event.IsChecked():
      radio_dict[name] = "True"
      setattr(conf, name, True)
    else:
      radio_dict[name] = "False"
      setattr(conf, name, False)
    local_conf.settings_changed = True
    if hasattr(application.Hardware, "ChangeBandFilters"):
      application.Hardware.ChangeBandFilters()

class RadioFilters(BaseWindow):		# The Filters page in the second-level notebook for each radio
  def __init__(self, parent, radio_name):
    BaseWindow.__init__(self, parent)
    self.radio_name = radio_name
    self.controls_done = False
  def MakeControls(self):
    if self.controls_done:
      return
    self.controls_done = True
    radio_dict = local_conf.GetRadioDict(self.radio_name)
    self.num_cols = 8
    self.NextRow()
    bus_text = 'These high-pass and low-pass filters are only available for radios that support the Hermes protocol.\
  Enter a frequency range and the control bits for that range. Leave the frequencies blank for unused ranges.\
  Place whole bands within the frequency ranges because filters are only changed when changing bands.\
  Check the bit for a "1", and uncheck the bit for a "0".\
  Bits are shown in binary number order.  For example, decimal 9 is 0b1001, so check bits 3 and 0.\
  Changes are immediate (no need to restart).\
  Refer to the documentation for your filter board to see which bits to set.\
  The Rx bits are used for both receive and transmit, unless the "Tx Enable" box is checked.\
  Then you can specify different filters for Rx and Tx.\
  If multiple receivers are in use, the filters will accommodate the highest and lowest frequencies of all receivers.'
    self.AddTextCHelp(1, 'Hermes Protocol: Alex High and Low Pass Filters', bus_text, span=self.num_cols)
    self.NextRow()
    self.AddTextC(1, 'Start MHz')
    self.AddTextC(2, 'End MHz')
    self.AddTextC(3, "Alex HPF Rx")
    btn = self.AddCheckBox(4, "Alex HPF Tx", self.ChangeEnable, flag=wx.ALIGN_CENTER|wx.ALIGN_CENTER_VERTICAL)
    btn.quisk_data_name = "AlexHPF_TxEn"
    value = self.GetValue("AlexHPF_TxEn", radio_dict)
    value = value == 'True'
    btn.SetValue(value)
    self.AddTextC(5, 'Start MHz')
    self.AddTextC(6, 'End MHz')
    self.AddTextC(7, "Alex LPF Rx")
    btn = self.AddCheckBox(8, "Alex LPF Tx", self.ChangeEnable, flag=wx.ALIGN_CENTER|wx.ALIGN_CENTER_VERTICAL)
    btn.quisk_data_name = "AlexLPF_TxEn"
    value = self.GetValue("AlexLPF_TxEn", radio_dict)
    value = value == 'True'
    btn.SetValue(value)
    self.NextRow()
    hp_filters = self.GetValue("AlexHPF", radio_dict)
    lp_filters = self.GetValue("AlexLPF", radio_dict)
    row = self.row
    for index in range(len(hp_filters)):
      f1, f2, rx, tx = hp_filters[index]	# f1 and f2 are strings; rx and tx are integers
      cb = self.AddTextCtrl(1, f1, self.OnChangeFreq)
      cb.quisk_data_name = "AlexHPF"
      cb.index = (index, 0)
      cb = self.AddTextCtrl(2, f2, self.OnChangeFreq)
      cb.quisk_data_name = "AlexHPF"
      cb.index = (index, 1)
      bf = self.AddBitField(3, 8, 'AlexHPF', None, rx, self.ChangeBits)
      bf.index = (index, 2)
      bf = self.AddBitField(4, 8, 'AlexHPF', None, tx, self.ChangeBits)
      bf.index = (index, 3)
      self.NextRow()
      index += 1
    self.row = row
    for index in range(len(lp_filters)):
      f1, f2, rx, tx = lp_filters[index]	# f1 and f2 are strings; rx and tx are integers
      cb = self.AddTextCtrl(5, f1, self.OnChangeFreq)
      cb.quisk_data_name = "AlexLPF"
      cb.index = (index, 0)
      cb = self.AddTextCtrl(6, f2, self.OnChangeFreq)
      cb.quisk_data_name = "AlexLPF"
      cb.index = (index, 1)
      bf = self.AddBitField(7, 8, 'AlexLPF', None, rx, self.ChangeBits)
      bf.index = (index, 2)
      bf = self.AddBitField(8, 8, 'AlexLPF', None, tx, self.ChangeBits)
      bf.index = (index, 3)
      self.NextRow()
      index += 1
    self.FitInside()
    self.SetScrollRate(1, 1)
  def OnChangeFreq(self, event):
    freq = event.GetString()
    radio_dict = local_conf.GetRadioDict(self.radio_name)
    ctrl = event.GetEventObject()
    name = ctrl.quisk_data_name
    filters = self.GetValue(name, radio_dict)
    filters[ctrl.index[0]][ctrl.index[1]] = freq
    setattr(conf, name, filters)
    radio_dict[name] = filters
    local_conf.settings_changed = True
    if hasattr(application.Hardware, "ChangeAlexFilters"):
      application.Hardware.ChangeAlexFilters(edit=True)
  def ChangeBits(self, control):
    radio_dict = local_conf.GetRadioDict(self.radio_name)
    name = control.quisk_data_name
    filters = self.GetValue(name, radio_dict)
    filters[control.index[0]][control.index[1]] = control.value
    setattr(conf, name, filters)
    radio_dict[name] = filters
    local_conf.settings_changed = True
    if hasattr(application.Hardware, "ChangeAlexFilters"):
      application.Hardware.ChangeAlexFilters(edit=True)
  def ChangeEnable(self, event):
    btn = event.GetEventObject()
    name = btn.quisk_data_name
    radio_dict = local_conf.GetRadioDict(self.radio_name)
    if event.IsChecked():
      radio_dict[name] = "True"
      setattr(conf, name, True)
    else:
      radio_dict[name] = "False"
      setattr(conf, name, False)
    local_conf.settings_changed = True
    if hasattr(application.Hardware, "ChangeAlexFilters"):
      application.Hardware.ChangeAlexFilters(edit=True)
