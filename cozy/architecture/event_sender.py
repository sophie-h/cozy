from gi.repository import Gdk, GLib


class EventSender:
    __listeners = []

    def emit_event(self, event, message=None):
        if type(event) is tuple and not message:
            message = event[1]
            event = event[0]

        for function in self.__listeners:
            function(event, message)

    def emit_event_main_thread(self, event, message=None):
        Gdk.threads_add_idle(GLib.PRIORITY_DEFAULT_IDLE, self.emit_event, (event, message))

    def add_listener(self, function):
        self.__listeners.append(function)
