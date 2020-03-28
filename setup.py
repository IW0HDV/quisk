from __future__ import print_function

from setuptools import setup, Extension
import sys
import os
import struct

# You must define the version here.  A title string including
# the version will be written to __init__.py and read by quisk.py.

Version = '4.1.53'

fp = open("__init__.py", "w")	# write title string
fp.write("#Quisk version %s\n" % Version)
fp.close()

is_64bit = struct.calcsize("P") == 8

if sys.platform != "win32":
  try:
    import wx
  except ImportError:
    print ("Please install the package python-wxgtk3.0 or later")
  if not os.path.isfile("/usr/include/fftw3.h"):
    print ("Please install the package libfftw3-dev")
  if not os.path.isdir("/usr/include/alsa"):
    print ("Please install the package libasound2-dev")
  if not os.path.isfile("/usr/include/portaudio.h"):
    print ("Please install the package portaudio19-dev")
  if not os.path.isdir("/usr/include/pulse"):
    print ("please install the package libpulse-dev")

module1 = Extension ('quisk._quisk',
	libraries = ['asound', 'portaudio', 'pulse', 'fftw3', 'm'],
	sources = ['quisk.c', 'sound.c', 'sound_alsa.c', 'sound_portaudio.c', 'sound_pulseaudio.c',
		'is_key_down.c', 'microphone.c', 'utility.c',
		'filter.c', 'extdemod.c', 'freedv.c'],
	)

module2 = Extension ('quisk.sdriqpkg.sdriq',
	libraries = ['m'],
	sources = ['import_quisk_api.c', 'sdriqpkg/sdriq.c'],
	include_dirs = ['.'],
	)

# Afedri hardware support added by Alex, Alex@gmail.com
module3 = Extension ('quisk.afedrinet.afedrinet_io',
	libraries = ['m'],
	sources = ['import_quisk_api.c', 'is_key_down.c', 'afedrinet/afedrinet_io.c'],
	include_dirs = ['.'],
	)

modulew1 = Extension ('quisk._quisk',
	include_dirs = ['../fftw3'],
	#include_dirs = ['../fftw3', 'C:/Program Files (x86)/Microsoft DirectX SDK (February 2010)/Include',
	#     'C:/Program Files/Microsoft DirectX SDK (February 2010)/Include',],
	library_dirs = ['../fftw3'],
	libraries = ['fftw3-3', 'WS2_32', 'Dxguid', 'Dsound', 'iphlpapi'],
	sources = ['quisk.c', 'sound.c', 'sound_directx.c',
		'is_key_down.c', 'microphone.c', 'utility.c',
		'filter.c', 'extdemod.c', 'freedv.c'],
	)

modulew2 = Extension ('quisk.sdriqpkg.sdriq',
	libraries = [':ftd2xx.lib'],
	library_dirs = ['../ftdi/i386'],
	sources = ['import_quisk_api.c', 'sdriqpkg/sdriq.c'],
	include_dirs = ['.', '../ftdi'],
	#extra_link_args = ['--enable-auto-import'],
	)

# Afedri hardware support added by Alex, Alex@gmail.com
modulew3 = Extension ('quisk.afedrinet.afedrinet_io',
	libraries = ['WS2_32'],
	sources = ['import_quisk_api.c', 'is_key_down.c', 'afedrinet/afedrinet_io.c'],
	include_dirs = ['.'],
	)

modulew4 = Extension ('quisk.soapypkg.soapy',
	sources = ['import_quisk_api.c', 'soapypkg/soapy.c'],
	include_dirs = [".", "c:/Program Files/PothosSDR/include"],
	libraries = ['WS2_32', 'SoapySDR'],
	)

# Changes for MacOS support thanks to Mario, DL3LSM.
# Changes by Jim, N1ADJ.
modulem1 = Extension ('quisk._quisk',
	#include_dirs = ['.'],
	#library_dirs = ['.'],
	libraries = ['portaudio', 'fftw3', 'm', 'pulse'],
	sources = ['quisk.c', 'sound.c', 'sound_portaudio.c',
		'is_key_down.c', 'microphone.c', 'utility.c',
		'filter.c', 'extdemod.c', 'freedv.c', 'sound_pulseaudio.c'],
	)

modulem2 = Extension ('quisk.sdriqpkg.sdriq',
	#libraries = [':_quisk.so', 'm'],
	libraries = ['m', 'ftd2xx'],
	sources = ['import_quisk_api.c', 'sdriqpkg/sdriq.c'],
	include_dirs = ['.', '..', '/usr/local/include'],
	library_dirs = ['.', '/usr/local/lib'],
	#include_dirs = ['.', '..', '/opt/local/include'],
	#library_dirs = ['.', '/opt/local/lib'],
	#runtime_library_dirs = ['.'],
	)

# Changes for building from macports provided by Eric, KM4DSJ
modulemp1 = Extension ('quisk._quisk',
	include_dirs = ['.', '/opt/local/include'],
	library_dirs = ['.', '/opt/local/lib'],
	libraries = ['portaudio', 'fftw3', 'm', 'pulse'],
	sources = ['quisk.c', 'sound.c', 'sound_portaudio.c',
		'is_key_down.c', 'microphone.c', 'utility.c',
		'filter.c', 'extdemod.c', 'freedv.c', 'sound_pulseaudio.c'],
	)

modulemp2 = Extension ('quisk.sdriqpkg.sdriq',
	#libraries = [':_quisk.so', 'm'],
	libraries = ['m', 'ftd2xx'],
	sources = ['import_quisk_api.c', 'sdriqpkg/sdriq.c'],
	include_dirs = ['.', '..', '/opt/local/include'],
	library_dirs = ['.', '/opt/local/lib'],
	#runtime_library_dirs = ['.'],
	)

if sys.platform == "win32":
  Modules = [modulew1, modulew2, modulew3]
  if is_64bit:
    Modules.append(modulew4)
  requires = ['wxPython', 'pyusb']
elif sys.platform == "darwin" and os.path.exists('/opt/local/lib'):
  Modules = [modulemp1, modulemp2]
  requires = ['wxPython', 'pyusb']
elif sys.platform == "darwin":
  Modules = [modulem1, modulem2]
  requires = ['wxPython', 'pyusb']
else:
  Modules = [module1, module2, module3]
  requires = []

setup	(name = 'quisk',
	version = Version,
	description = 'QUISK is a Software Defined Radio (SDR) transceiver that can control various radio hardware.',
	long_description = """QUISK is a Software Defined Radio (SDR) transceiver.  
You supply radio hardware that converts signals at the antenna to complex (I/Q) data at an
intermediate frequency (IF). Data can come from a sound card, Ethernet or USB. Quisk then filters and
demodulates the data and sends the audio to your speakers or headphones. For transmit, Quisk takes
the microphone signal, converts it to I/Q data and sends it to the hardware.

Quisk can be used with SoftRock, Hermes Lite 2, HiQSDR, Odyssey and many radios that use the Hermes protocol.
Quisk can connect to digital programs like Fldigi and WSJT-X. Quisk can be connected to other software like
N1MM+ and software that uses Hamlib.
""",
	author = 'James C. Ahlstrom',
	author_email = 'jahlstr@gmail.com',
	url = 'http://james.ahlstrom.name/quisk/',
	packages = ['quisk', 'quisk.sdriqpkg', 'quisk.n2adr', 'quisk.softrock', 'quisk.freedvpkg',
		'quisk.hermes', 'quisk.hiqsdr', 'quisk.afedrinet', 'quisk.soapypkg'],
	package_dir =  {'quisk' : '.'},
	package_data = {'' : ['*.txt', '*.html', '*.so', '*.dll']},
	entry_points = {'gui_scripts' : ['quisk = quisk.quisk:main', 'quisk_vna = quisk.quisk_vna:main']},
	ext_modules = Modules,
	install_requires = requires,
	provides = ['quisk'],
	classifiers = [
		'Development Status :: 6 - Mature',
		'Environment :: X11 Applications',
		'Environment :: Win32 (MS Windows)',
		'Intended Audience :: End Users/Desktop',
		'License :: OSI Approved :: GNU General Public License (GPL)',
		'Natural Language :: English',
		'Operating System :: POSIX :: Linux',
		'Operating System :: Microsoft :: Windows',
		'Programming Language :: Python :: 2.7',
		'Programming Language :: Python :: 3',
		'Programming Language :: C',
		'Topic :: Communications :: Ham Radio',
	],
)


