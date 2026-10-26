# floatingMenu.py

import ctypes

import addonHandler
import core
import gui
import ui
import wx
from logHandler import log
from speech.extensions import filter_speechSequence

addonHandler.initTranslation()

VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002

_activeFrame = None
_speechMuted = False

# The frame title and the "list" role are announced as soon as the menu takes the
# focus. Speech is held back for this short window so the first item is the first
# thing heard. The window ends early on the first navigation key, and a hard
# deadline guarantees speech always comes back.
_MUTE_SETTLE_MS = 300
_MUTE_DEADLINE_MS = 1500


def _muteHandler(speechSequence, *args, **kwargs):
	if _speechMuted:
		return []
	return speechSequence


def _setSpeechMuted(muted):
	global _speechMuted
	if muted == _speechMuted:
		return
	_speechMuted = muted
	if muted:
		filter_speechSequence.register(_muteHandler)
	else:
		filter_speechSequence.unregister(_muteHandler)


def _cleanLabel(label):
	return label.replace("&&", "\0").replace("&", "").replace("\0", "&")


def _restoreForeground(hwnd):
	if not hwnd:
		return
	user32 = ctypes.windll.user32
	# A harmless Alt tap lets SetForegroundWindow succeed from a process that does not hold the input focus.
	user32.keybd_event(VK_MENU, 0, 0, 0)
	user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
	user32.SetForegroundWindow(hwnd)


class _MenuFrame(wx.Frame):

	def __init__(self, title, entries, restoreHwnd):
		super().__init__(None, title=title, style=wx.CAPTION | wx.CLOSE_BOX | wx.STAY_ON_TOP)
		self._entries = entries
		self._restoreHwnd = restoreHwnd
		self._finished = False
		self._timers = []
		self.listBox = wx.ListBox(self, choices=[_cleanLabel(label) for label, _action in entries], style=wx.LB_SINGLE)
		self.listBox.SetSelection(0)
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(self.listBox, 1, wx.EXPAND | wx.ALL, 6)
		self.SetSizer(sizer)
		self.SetClientSize((320, min(40 + 22 * len(entries), 420)))
		self.Centre()
		self.Bind(wx.EVT_CHAR_HOOK, self._onCharHook)
		self.Bind(wx.EVT_ACTIVATE, self._onActivate)
		self.Bind(wx.EVT_CLOSE, self._onClose)
		self.listBox.Bind(wx.EVT_LISTBOX_DCLICK, lambda event: self._choose())
		self.listBox.Bind(wx.EVT_SET_FOCUS, self._onListFocus)

	def beginQuietOpen(self):
		_setSpeechMuted(True)
		self._timers.append(wx.CallLater(_MUTE_DEADLINE_MS, self._releaseSpeech, False))

	def _onListFocus(self, event):
		event.Skip()
		if _speechMuted and not self._finished:
			self._timers.append(wx.CallLater(_MUTE_SETTLE_MS, self._releaseSpeech, True))

	def _releaseSpeech(self, announceFirstItem):
		if not _speechMuted:
			return
		_setSpeechMuted(False)
		if not announceFirstItem or self._finished:
			return
		index = self.listBox.GetSelection()
		if index == wx.NOT_FOUND:
			return
		position = _("{index} of {total}").format(index=index + 1, total=self.listBox.GetCount())
		ui.message(f"{self.listBox.GetString(index)} {position}")

	def _cancelTimers(self):
		for timer in self._timers:
			timer.Stop()
		self._timers = []

	def _onCharHook(self, event):
		code = event.GetKeyCode()
		if _speechMuted and code in (wx.WXK_UP, wx.WXK_DOWN, wx.WXK_HOME, wx.WXK_END):
			self._releaseSpeech(False)
		if code in (wx.WXK_UP, wx.WXK_DOWN) and not event.HasModifiers() and self._wrap(code):
			return
		if code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
			self._choose()
		elif code == wx.WXK_ESCAPE:
			self._finish(None)
		else:
			event.Skip()

	def _wrap(self, code):
		"""Cycle past either end of the list; returns True when the key was handled here."""
		count = self.listBox.GetCount()
		index = self.listBox.GetSelection()
		if count < 2 or index == wx.NOT_FOUND:
			return False
		if code == wx.WXK_UP and index == 0:
			target = count - 1
		elif code == wx.WXK_DOWN and index == count - 1:
			target = 0
		else:
			return False
		self.listBox.SetSelection(target)
		position = _("{index} of {total}").format(index=target + 1, total=count)
		ui.message(f"{self.listBox.GetString(target)} {position}")
		return True

	def _onActivate(self, event):
		if not event.GetActive() and not self._finished:
			wx.CallAfter(self._finish, None)
		event.Skip()

	def _onClose(self, event):
		self._finish(None)

	def _choose(self):
		index = self.listBox.GetSelection()
		if index == wx.NOT_FOUND:
			return
		self._finish(self._entries[index][1])

	def _finish(self, action):
		global _activeFrame
		if self._finished:
			return
		self._finished = True
		if _activeFrame is self:
			_activeFrame = None
		restoreHwnd = self._restoreHwnd
		self._cancelTimers()
		_setSpeechMuted(False)
		self.Destroy()
		if action is not None:
			core.callLater(0, action)
			return
		wx.CallAfter(_restoreForeground, restoreHwnd)


def showFloatingMenu(title, entries):
	"""Open the menu as a small frame; entries is a list of (label, callable). Returns the frame."""
	global _activeFrame
	if not entries:
		return None
	closeFloatingMenu()
	restoreHwnd = ctypes.windll.user32.GetForegroundWindow()
	frame = _MenuFrame(title, entries, restoreHwnd)
	_activeFrame = frame
	frame.beginQuietOpen()
	frame.Show()
	frame.Raise()
	frame.listBox.SetFocus()
	return frame


def updateMenuLabel(frame, index, label):
	if frame is not _activeFrame or frame._finished:
		return
	if not 0 <= index < frame.listBox.GetCount():
		return
	frame.listBox.SetString(index, _cleanLabel(label))


def closeFloatingMenu():
	global _activeFrame
	frame = _activeFrame
	_activeFrame = None
	if frame is None:
		return
	try:
		frame._finished = True
		frame._cancelTimers()
		_setSpeechMuted(False)
		frame.Destroy()
	except RuntimeError as e:
		log.debug(f"xTrack: floating menu was already gone: {e}")


class _ChoiceDialog(wx.Dialog):

	def __init__(self, parent, title, labels):
		super().__init__(parent, title=title)
		self.listBox = wx.ListBox(self, choices=labels, style=wx.LB_SINGLE)
		self.listBox.SetSelection(0)
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(self.listBox, 1, wx.EXPAND | wx.ALL, 6)
		self.SetSizer(sizer)
		self.SetClientSize((320, min(40 + 22 * len(labels), 420)))
		self.CentreOnParent()
		self.Bind(wx.EVT_CHAR_HOOK, self._onCharHook)
		self.listBox.Bind(wx.EVT_LISTBOX_DCLICK, lambda event: self._accept())

	def _onCharHook(self, event):
		code = event.GetKeyCode()
		if code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
			self._accept()
		elif code == wx.WXK_ESCAPE:
			self.EndModal(wx.ID_CANCEL)
		else:
			event.Skip()

	def _accept(self):
		if self.listBox.GetSelection() == wx.NOT_FOUND:
			return
		self.EndModal(wx.ID_OK)


def chooseMenuAction(parent, title, entries):
	"""Pick one action from a list inside an already open dialog; entries are (label, callable or sub-list).

	Escape in a sub-list goes back one level, Escape at the top closes the menu. The chosen action
	runs after the menu window is gone, so it never executes inside the menu's own modal loop.
	"""
	levels = [(title, entries)]
	chosen = None
	while levels and chosen is None:
		levelTitle, levelEntries = levels[-1]
		dialog = _ChoiceDialog(parent, levelTitle, [_cleanLabel(label) for label, _item in levelEntries])
		try:
			if dialog.ShowModal() != wx.ID_OK:
				levels.pop()
				continue
			label, item = levelEntries[dialog.listBox.GetSelection()]
		finally:
			dialog.Destroy()
		if callable(item):
			chosen = item
		else:
			levels.append((_cleanLabel(label), item))
	if chosen is not None:
		wx.CallAfter(chosen)
