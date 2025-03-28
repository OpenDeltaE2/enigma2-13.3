import struct
import os
import time
from Components.config import config, ConfigSelection, ConfigYesNo, ConfigSubsection, ConfigText, ConfigCECAddress, ConfigLocations, ConfigDirectory, ConfigNothing, ConfigIP, ConfigInteger, ConfigSubList
import urllib.request
from Components.Console import Console
from enigma import eHdmiCEC, eActionMap
from Tools.StbHardware import getFPWasTimerWakeup
import NavigationInstance
from enigma import eTimer
from sys import maxsize

LOGPATH = "/hdd/"
LOGFILE = "hdmicec.log"

# CEC Version's table
CEC = ["1.1", "1.2", "1.2a", "1.3", "1.3a", "1.4", "2.0?", "unknown"]

cmdList = {
	0x00: "<Polling Message>",
	0x04: "<Image View On>",
	0x0d: "<Text View On>",
	0x32: "<Set Menu Language>",
	0x36: "<Standby>",
	0x46: "<Give OSD Name>",
	0x47: "<Set OSD Name>",
	0x70: "<System Mode Audio Request>",
	0x71: "<Give Audio Status>",
	0x72: "<Set System Audio Mode>",
	0x7a: "<Report Audio Status>",
	0x7d: "<Give System Audio Mode Status>",
	0x7e: "<System Audio Mode Status>",
	0x80: "<Routing Change>",
	0x81: "<Routing Information>",
	0x82: "<Active Source>",
	0x83: "<Give Physical Address>",
	0x84: "<Report Physical Address>",
	0x85: "<Request Active Source>",
	0x86: "<Set Stream Path>",
	0x87: "<Device Vendor ID>",
	0x89: "<Vendor Command>",
	0x8c: "<Give Device Vendor ID>",
	0x8d: "<Menu Request>",
	0x8e: "<Menu Status>",
	0x8f: "<Give Device Power Status>",
	0x90: "<Report Power Status>",
	0x91: "<Get menu language>",
	0x9e: "<CEC Version>",
	0x9d: "<Inactive Source>",
	0x9e: "<CEC Version>",
	0x9f: "<Get CEC Version>",
	0xa0: "<Vendor Command With ID>",
	0xa1: "<Clear External Timer>",
	0xa2: "<Set External Timer>",
	0xff: "<Abort>"
	}

config.hdmicec = ConfigSubsection()
config.hdmicec.enabled = ConfigYesNo(default=False)
config.hdmicec.control_tv_standby = ConfigYesNo(default=True)
config.hdmicec.control_tv_wakeup = ConfigYesNo(default=True)
config.hdmicec.report_active_source = ConfigYesNo(default=True)
config.hdmicec.report_active_menu = ConfigYesNo(default=True)
config.hdmicec.handle_tv_standby = ConfigYesNo(default=True)
config.hdmicec.handle_tv_wakeup = ConfigYesNo(default=True)
config.hdmicec.tv_wakeup_detection = ConfigSelection(
	choices={
	"wakeup": _("Wakeup"),
	"requestphysicaladdress": _("Request for physical address report"),
	"tvreportphysicaladdress": _("TV physical address report"),
	"sourcerequest": _("Source request"),
	"streamrequest": _("Stream request"),
	"requestvendor": _("Request for vendor report"),
	"osdnamerequest": _("OSD name request"),
	"activity": _("Any activity"),
	},
	default="streamrequest")
config.hdmicec.tv_wakeup_command = ConfigSelection(
	choices={
	"imageview": _("Image View On"),
	"textview": _("Text View On"),
	},
	default="imageview")
config.hdmicec.fixed_physical_address = ConfigText(default="0.0.0.0")
config.hdmicec.volume_forwarding = ConfigYesNo(default=False)
choicelist = []
for i in range(1, 31):
	choicelist.append((str(i), str(i)))
config.hdmicec.volume_forwarding_repeat = ConfigSelection(default="0", choices=[("0", _("Disabled"))] + choicelist)
config.hdmicec.volume_forwarding_repeat_empty = ConfigNothing()
config.hdmicec.control_receiver_wakeup = ConfigYesNo(default=False)
config.hdmicec.control_receiver_standby = ConfigYesNo(default=False)
config.hdmicec.handle_deepstandby_events = ConfigSelection(default="no", choices=[("no", _("No")), ("yes", _("Yes")), ("poweroff", _("Only power off"))])
choicelist = []
for i in (10, 50, 100, 150, 250, 500, 750, 1000):
	choicelist.append(("%d" % i, _("%d ms") % i))
config.hdmicec.minimum_send_interval = ConfigSelection(default="0", choices=[("0", _("Disabled"))] + choicelist)
choicelist = []
for i in [3] + list(range(5, 65, 5)):
	choicelist.append(("%d" % i, _("%d sec") % i))
config.hdmicec.repeat_wakeup_timer = ConfigSelection(default="3", choices=[("0", _("Disabled"))] + choicelist)
config.hdmicec.debug = ConfigSelection(default="0", choices=[("0", _("Disabled")), ("1", _("Messages")), ("2", _("Key Events")), ("3", _("All"))])
config.hdmicec.bookmarks = ConfigLocations(default=[LOGPATH])
config.hdmicec.log_path = ConfigDirectory(LOGPATH)
config.hdmicec.next_boxes_detect = ConfigYesNo(default=False)
config.hdmicec.sourceactive_zaptimers = ConfigYesNo(default=False)
config.hdmicec.ethernet_pc_used = ConfigYesNo(default=False)
config.hdmicec.pc_ip = ConfigIP(default = [192,168,3,7])

config.hdmicec.ethbox = ConfigSubList()
def create_box(ip=[192, 168, 1, 1], port=80, used=False):
    box = ConfigSubsection()
    box.used = ConfigYesNo(default=used)
    box.ip = ConfigIP(default=ip)
    box.port = ConfigInteger(default=port, limits=(1, 65535))
    return box
def add_box(ip, port, used=False):
	config.hdmicec.ethbox.append(create_box(ip=ip, port=port, used=used))
add_box([192, 168, 3, 41], 80)
add_box([192, 168, 3, 43], 80)


class HdmiCec:

	instance = None

	def __init__(self):
		assert not HdmiCec.instance, "only one HdmiCec instance is allowed!"
		HdmiCec.instance = self

		self.wait = eTimer()
		self.wait.timeout.get().append(self.sendCmd)
		self.waitKeyEvent = eTimer()
		self.waitKeyEvent.timeout.get().append(self.sendKeyEvent)
		self.queueKeyEvent = []
		self.repeat = eTimer()
		self.repeat.timeout.get().append(self.wakeupMessages)
		self.queue = []

		self.delayEthernetPC = eTimer()
		self.delayEthernetPC.timeout.get().append(self.ethernetPCActive)

		self.delayEthernetBox = eTimer()
		self.delayEthernetBox.timeout.get().append(self.ethernetBoxActive)

		self.delay = eTimer()
		self.delay.timeout.get().append(self.sendStandbyMessages)
		self.useStandby = True

		self.handlingStandbyFromTV = False

		eHdmiCEC.getInstance().messageReceived.get().append(self.messageReceived)
		config.misc.standbyCounter.addNotifier(self.onEnterStandby, initial_call=False)
		config.misc.DeepStandby.addNotifier(self.onEnterDeepStandby, initial_call=False)
		self.setFixedPhysicalAddress(config.hdmicec.fixed_physical_address.value)

		self.volumeForwardingEnabled = False
		self.volumeForwardingDestination = 0
		self.wakeup_from_tv = False
		eActionMap.getInstance().bindAction('', -maxsize - 1, self.keyEvent)
		config.hdmicec.volume_forwarding.addNotifier(self.configVolumeForwarding, initial_call=False)
		config.hdmicec.enabled.addNotifier(self.configVolumeForwarding)
		if config.hdmicec.enabled.value:
			if config.hdmicec.report_active_menu.value:
				if config.hdmicec.report_active_source.value and NavigationInstance.instance and not NavigationInstance.instance.isRestartUI():
					self.sendMessage(0, "sourceinactive")
				self.sendMessage(0, "menuactive")
			if config.hdmicec.handle_deepstandby_events.value == "yes" and (not getFPWasTimerWakeup() or (config.usage.startup_to_standby.value == "no" and config.misc.prev_wakeup_time_type.value == 3)):
				self.onLeaveStandby()

	def getPhysicalAddress(self):
		physicaladdress = eHdmiCEC.getInstance().getPhysicalAddress()
		hexstring = '%04x' % physicaladdress
		return hexstring[0] + '.' + hexstring[1] + '.' + hexstring[2] + '.' + hexstring[3]

	def setFixedPhysicalAddress(self, address):
		if address != config.hdmicec.fixed_physical_address.value:
			config.hdmicec.fixed_physical_address.value = address
			config.hdmicec.fixed_physical_address.save()
		hexstring = address[0] + address[2] + address[4] + address[6]
		eHdmiCEC.getInstance().setFixedPhysicalAddress(int(float.fromhex(hexstring)))

	def sendMessage(self, address, message):
		cmd = 0
		data = b''
		if message == "wakeup":
			if config.hdmicec.tv_wakeup_command.value == 'textview':
				cmd = 0x0d
			else:
				cmd = 0x04
		elif message == "sourceactive":
			address = 0x0f # use broadcast for active source command
			cmd = 0x82
			data = self.setData()
		elif message == "standby":
			cmd = 0x36
		elif message == "sourceinactive":
			cmd = 0x9d
			data = self.setData()
		elif message == "menuactive":
			cmd = 0x8e
			data = struct.pack('B', 0x00)
		elif message == "menuinactive":
			cmd = 0x8e
			data = struct.pack('B', 0x01)
		elif message == "givesystemaudiostatus":
			cmd = 0x7d
			address = 0x05
		elif message == "setsystemaudiomode":
			cmd = 0x70
			address = 0x05
			data = self.setData()
		elif message == "osdname":
			cmd = 0x47
			data = os.uname()[1]
			data = data[:14].encode()
		elif message == "poweractive":
			cmd = 0x90
			data = struct.pack('B', 0x00)
		elif message == "powerinactive":
			cmd = 0x90
			data = struct.pack('B', 0x01)
		elif message == "reportaddress":
			address = 0x0f # use broadcast address
			cmd = 0x84
			data = self.setData(True)
		elif message == "vendorid":
			cmd = 0x87
			data = b'\x00\x00\x00'
		elif message == "keypoweron":
			cmd = 0x44
			data = struct.pack('B', 0x6d)
		elif message == "keypoweroff":
			cmd = 0x44
			data = struct.pack('B', 0x6c)
		elif message == "sendcecversion":
			cmd = 0x9E
			data = struct.pack('B', 0x04) # v1.3a
		elif message == "requestactivesource":
			address = 0x0f # use broadcast address
			cmd = 0x85
		elif message == "getpowerstatus":
			self.useStandby = True
			address = 0x0f # use broadcast address => boxes will send info
			cmd = 0x8f

		if cmd:
			try:
				data = data.decode("UTF-8")
			except:
				data = data.decode("ISO-8859-1")
			if config.hdmicec.minimum_send_interval.value != "0":
				self.queue.append((address, cmd, data))
				if not self.wait.isActive():
					self.wait.start(int(config.hdmicec.minimum_send_interval.value), True)
			else:
				eHdmiCEC.getInstance().sendMessage(address, cmd, data, len(data))
			if config.hdmicec.debug.value in ["1", "3"]:
				self.debugTx(address, cmd, data)

	def sendCmd(self):
		if len(self.queue):
			(address, cmd, data) = self.queue.pop(0)
			eHdmiCEC.getInstance().sendMessage(address, cmd, data, len(data))
			self.wait.start(int(config.hdmicec.minimum_send_interval.value), True)

	def sendMessages(self, address, messages):
		for message in messages:
			self.sendMessage(address, message)

	def setData(self, devicetypeSend=False):
		physicaladdress = eHdmiCEC.getInstance().getPhysicalAddress()
		if devicetypeSend:
			devicetype = eHdmiCEC.getInstance().getDeviceType()
			return struct.pack('BBB', int(physicaladdress / 256), int(physicaladdress % 256), devicetype)
		return struct.pack('BB', int(physicaladdress / 256), int(physicaladdress % 256))

	def wakeupMessages(self):
		if config.hdmicec.enabled.value:
			messages = []
			if config.hdmicec.control_tv_wakeup.value:
				if not self.wakeup_from_tv:
					messages.append("wakeup")
			self.wakeup_from_tv = False
			if config.hdmicec.report_active_source.value:
				messages.append("sourceactive")
			if config.hdmicec.report_active_menu.value:
				messages.append("menuactive")
			if messages:
				self.sendMessages(0, messages)

			if config.hdmicec.control_receiver_wakeup.value:
				self.sendMessage(5, "keypoweron")
				self.sendMessage(5, "setsystemaudiomode")

	def standbyMessages(self):
		if config.hdmicec.enabled.value:
			if config.hdmicec.next_boxes_detect.value:
				self.secondBoxActive()
				self.delay.start(1000, True)
			else:
				self.sendStandbyMessages()

	def sendStandbyMessages(self):
			messages = []
			if config.hdmicec.control_tv_standby.value:
				if self.useStandby and not self.handlingStandbyFromTV:
					messages.append("standby")
				else:
					messages.append("sourceinactive")
					self.useStandby = True
			else:
				if config.hdmicec.report_active_source.value:
					messages.append("sourceinactive")
				if config.hdmicec.report_active_menu.value:
					messages.append("menuinactive")
			if messages:
				self.sendMessages(0, messages)

			if config.hdmicec.control_receiver_standby.value:
				self.sendMessage(5, "keypoweroff")
				self.sendMessage(5, "standby")

	def secondBoxActive(self):
		if config.hdmicec.ethernet_pc_used.value:
			self.delayEthernetPC.start(100, True)
		if any(box.used.value for box in config.hdmicec.ethbox):
			self.delayEthernetBox.start(200, True)
		self.sendMessage(0, "getpowerstatus")

	def ethernetPCActive(self):
		def result(data, retval, extra):
			if retval == 0:
				self.useStandby = False
				print("[HDMI-CEC] found PC corresponding from address %s" % ip)
		if config.hdmicec.ethernet_pc_used.value:
			ip = "%d.%d.%d.%d" % tuple(config.hdmicec.pc_ip.value)
			cmd = "ping -c 1 -W 1 %s >/dev/null 2>&1" % ip
			Console().ePopen(cmd, result)

	def ethernetBoxActive(self):
		def getEthernetBoxActive(ip, port):
			try:
				response = urllib.request.urlopen("http://%s:%d/web/powerstate" % (ip, port))
				for line in response:
					if 'false' in line.decode('utf-8'):
						self.useStandby = False
						print("[HDMI-CEC] powered ethernet box %s found" % ip)
			except Exception as e:
				print("[HDMI-CEC] error", e)

		for box in config.hdmicec.ethbox:
			if not self.useStandby: # no further testing is needed
				break
			if box.used.value:
				ip = "%d.%d.%d.%d" % tuple(box.ip.value)
				port = box.port.value
				getEthernetBoxActive(ip, port)

	def onLeaveStandby(self):
		self.wakeupMessages()
		if int(config.hdmicec.repeat_wakeup_timer.value):
			self.repeat.startLongTimer(int(config.hdmicec.repeat_wakeup_timer.value))

	def onEnterStandby(self, configElement):
		from Screens.Standby import inStandby
		inStandby.onClose.append(self.onLeaveStandby)
		self.repeat.stop()
		self.standbyMessages()

	def onEnterDeepStandby(self, configElement):
		if config.hdmicec.enabled.value and config.hdmicec.handle_deepstandby_events.value != "no":
			if config.hdmicec.next_boxes_detect.value:
				self.delay.start(750, True)
			else:
				self.sendStandbyMessages()

	def standby(self):
		from Screens.Standby import Standby, inStandby
		if not inStandby:
			from Tools.Notifications import AddNotification
			AddNotification(Standby)

	def wakeup(self):
		self.wakeup_from_tv = True
		from Screens.Standby import inStandby
		if inStandby:
			inStandby.Power()

	def messageReceived(self, message):
		if config.hdmicec.enabled.value:
			from Screens.Standby import inStandby
			cmd = message.getCommand()
			data = 16 * '\x00'
			length = message.getData(data, len(data))
			ctrl0 = message.getControl0()
			ctrl1 = message.getControl1()
			ctrl2 = message.getControl2()

			if config.hdmicec.debug.value != "0":
				self.debugRx(length, cmd, data)
			if cmd == 0x00:
				if length == 0: # only polling message ( it's some as ping )
					print("eHdmiCec: received polling message")
				else:
					# feature abort
					if ctrl0 == 0x44:
						print('eHdmiCec: volume forwarding not supported by device %02x' % (message.getAddress()))
						self.volumeForwardingEnabled = False
			elif cmd == 0x46: # request name
				self.sendMessage(message.getAddress(), 'osdname')
			elif cmd == 0x7e or cmd == 0x72: # system audio mode status
				if ctrl0 == 0x01:
					self.volumeForwardingDestination = 5 # on: send volume keys to receiver
				else:
					self.volumeForwardingDestination = 0 # off: send volume keys to tv
				if config.hdmicec.volume_forwarding.value:
					print('eHdmiCec: volume forwarding to device %02x enabled' % (self.volumeForwardingDestination))
					self.volumeForwardingEnabled = True
			elif cmd == 0x8f: # request power status
				if inStandby:
					self.sendMessage(message.getAddress(), 'powerinactive')
				else:
					self.sendMessage(message.getAddress(), 'poweractive')
			elif cmd == 0x83: # request address
				self.sendMessage(message.getAddress(), 'reportaddress')
			elif cmd == 0x86: # request streaming path
				physicaladdress = ctrl0 * 256 + ctrl1
				ouraddress = eHdmiCEC.getInstance().getPhysicalAddress()
				if physicaladdress == ouraddress:
					if not inStandby:
						if config.hdmicec.report_active_source.value:
							self.sendMessage(message.getAddress(), 'sourceactive')
			elif cmd == 0x85: # request active source
				if not inStandby:
					if config.hdmicec.report_active_source.value:
						self.sendMessage(message.getAddress(), 'sourceactive')
			elif cmd == 0x8c: # request vendor id
				self.sendMessage(message.getAddress(), 'vendorid')
			elif cmd == 0x8d: # menu request
				if ctrl0 == 1: # query
					if inStandby:
						self.sendMessage(message.getAddress(), 'menuinactive')
					else:
						self.sendMessage(message.getAddress(), 'menuactive')
			elif cmd == 0x90: # receive powerstatus report
				if ctrl0 == 0: # some box is powered
					if config.hdmicec.next_boxes_detect.value:
						self.useStandby = False
					print("[HDMI-CEC] powered box found")
			elif cmd == 0x9F: # request get CEC version
				self.sendMessage(message.getAddress(), 'sendcecversion')

			# handle standby request from the tv
			if cmd == 0x36 and config.hdmicec.handle_tv_standby.value:
				# avoid echoing the 'System Standby' command back to the tv
				self.handlingStandbyFromTV = True
				# handle standby
				self.standby()
				# after handling the standby command, we are free to send 'standby' ourselves again
				self.handlingStandbyFromTV = False

			# handle wakeup requests from the tv
			if inStandby and config.hdmicec.handle_tv_wakeup.value:
				if cmd == 0x04 and config.hdmicec.tv_wakeup_detection.value == "wakeup":
					self.wakeup()
				elif cmd == 0x83 and config.hdmicec.tv_wakeup_detection.value == "requestphysicaladdress":
						self.wakeup()
				elif cmd == 0x84 and config.hdmicec.tv_wakeup_detection.value == "tvreportphysicaladdress":
					if (ctrl0 * 256 + ctrl1) == 0 and ctrl2 == 0:
						self.wakeup()
				elif cmd == 0x85 and config.hdmicec.tv_wakeup_detection.value == "sourcerequest":
					self.wakeup()
				elif cmd == 0x86 and config.hdmicec.tv_wakeup_detection.value == "streamrequest":
					physicaladdress = ctrl0 * 256 + ctrl1
					ouraddress = eHdmiCEC.getInstance().getPhysicalAddress()
					if physicaladdress == ouraddress:
						self.wakeup()
				elif cmd == 0x8C and config.hdmicec.tv_wakeup_detection.value == "requestvendor":
						self.wakeup()
				elif cmd == 0x46 and config.hdmicec.tv_wakeup_detection.value == "osdnamerequest":
					self.wakeup()
				elif cmd != 0x36 and config.hdmicec.tv_wakeup_detection.value == "activity":
					self.wakeup()

	def configVolumeForwarding(self, configElement):
		if config.hdmicec.enabled.value and config.hdmicec.volume_forwarding.value:
			self.volumeForwardingEnabled = True
			self.sendMessage(0x05, 'givesystemaudiostatus')
		else:
			self.volumeForwardingEnabled = False

	def keyEvent(self, keyCode, keyEvent):
		if not self.volumeForwardingEnabled:
			return
		cmd = 0
		data = b''
		if keyEvent == 0:
			if keyCode == 115:
				cmd = 0x44
				data = struct.pack('B', 0x41)
			if keyCode == 114:
				cmd = 0x44
				data = struct.pack('B', 0x42)
			if keyCode == 113:
				cmd = 0x44
				data = struct.pack('B', 0x43)
		if keyEvent == 2:
			if keyCode == 115:
				cmd = 0x44
				data = struct.pack('B', 0x41)
			if keyCode == 114:
				cmd = 0x44
				data = struct.pack('B', 0x42)
			if keyCode == 113:
				cmd = 0x44
				data = struct.pack('B', 0x43)
		if keyEvent == 1:
			if keyCode == 115 or keyCode == 114 or keyCode == 113:
				cmd = 0x45
		if cmd:
			try:
				data = data.decode("UTF-8")
			except:
				data = data.decode("ISO-8859-1")
			if config.hdmicec.minimum_send_interval.value != "0":
				self.queueKeyEvent.append((self.volumeForwardingDestination, cmd, data))
				repeat = int(config.hdmicec.volume_forwarding_repeat.value)
				if repeat and self.volumeForwardingDestination:
					for i in range(repeat):
						self.queueKeyEvent.append((self.volumeForwardingDestination, cmd, data))
				if not self.waitKeyEvent.isActive():
					self.waitKeyEvent.start(int(config.hdmicec.minimum_send_interval.value), True)
			else:
				eHdmiCEC.getInstance().sendMessage(self.volumeForwardingDestination, cmd, data, len(data))
			if config.hdmicec.debug.value in ["2", "3"]:
				self.debugTx(self.volumeForwardingDestination, cmd, data)
			return 1
		else:
			return 0

	def sendKeyEvent(self):
		if len(self.queueKeyEvent):
			(address, cmd, data) = self.queueKeyEvent.pop(0)
			eHdmiCEC.getInstance().sendMessage(address, cmd, data, len(data))
			self.waitKeyEvent.start(int(config.hdmicec.minimum_send_interval.value), True)

	def debugTx(self, address, cmd, data):
		txt = self.now(True) + self.opCode(cmd, True) + " " + "%02X" % (cmd) + " "
		tmp = ""
		if len(data):
			if cmd in [0x32, 0x47]:
				for i in range(len(data)):
					tmp += "%s" % data[i]
			else:
				for i in range(len(data)):
					tmp += "%02X" % ord(data[i]) + " "
		tmp += 48 * " "
		self.fdebug(txt + tmp[:48] + "[0x%02X]" % (address) + "\n")

	def debugRx(self, length, cmd, data):
		txt = self.now()
		if cmd == 0 and length == 0:
			txt += self.opCode(cmd) + " - "
		else:
			if cmd == 0:
				txt += "<Feature Abort>" + 13 * " " + "<  " + "%02X" % (cmd) + " "
			else:
				txt += self.opCode(cmd) + " " + "%02X" % (cmd) + " "
			for i in range(length - 1):
				if cmd in [0x32, 0x47]:
					txt += "%s" % data[i]
				elif cmd == 0x9e:
					txt += "%02X" % ord(data[i]) + 3 * " " + "[version: %s]" % CEC[ord(data[i])]
				else:
					txt += "%02X" % ord(data[i]) + " "
		txt += "\n"
		self.fdebug(txt)

	def opCode(self, cmd, out=False):
		send = "<"
		if out:
			send = ">"
		opCode = ''
		if cmd in cmdList:
			opCode += "%s" % cmdList[cmd]
		opCode += 30 * " "
		return opCode[:28] + send + " "

	def now(self, out=False, fulldate=False):
		send = "Rx: "
		if out:
			send = "Tx: "
		import datetime
		now = datetime.datetime.now()
		if fulldate:
			return send + now.strftime("%d-%m-%Y %H:%M:%S") + 2 * " "
		return send + now.strftime("%H:%M:%S") + 2 * " "

	def fdebug(self, output):
		from Tools.Directories import pathExists
		log_path = config.hdmicec.log_path.value
		path = os.path.join(log_path, LOGFILE)
		if pathExists(log_path):
			fp = open(path, 'a')
			fp.write(output)
			fp.close()


hdmi_cec = HdmiCec()
