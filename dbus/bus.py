# Copyright (C) 2007 Collabora Ltd. <http://www.collabora.co.uk/>
#
# Licensed under the Academic Free License version 2.1
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation; either version 2.1 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

__all__ = ('BusConnection',)
__docformat__ = 'reStructuredText'

import logging
import weakref

from _dbus_bindings import validate_interface_name, validate_member_name,\
                           validate_bus_name, validate_object_path,\
                           validate_error_name,\
                           BUS_SESSION, BUS_STARTER, BUS_SYSTEM, \
                           DBUS_START_REPLY_SUCCESS, \
                           DBUS_START_REPLY_ALREADY_RUNNING, \
                           BUS_DAEMON_NAME, BUS_DAEMON_PATH, BUS_DAEMON_IFACE,\
                           HANDLER_RESULT_NOT_YET_HANDLED
from dbus.connection import Connection
from dbus.exceptions import DBusException


_NAME_OWNER_CHANGE_MATCH = ("type='signal',sender='%s',"
                            "interface='%s',member='NameOwnerChanged',"
                            "path='%s',arg0='%%s'"
                            % (BUS_DAEMON_NAME, BUS_DAEMON_IFACE,
                               BUS_DAEMON_PATH))
"""(_NAME_OWNER_CHANGE_MATCH % sender) matches relevant NameOwnerChange
messages"""

_NAME_HAS_NO_OWNER = 'org.freedesktop.DBus.Error.NameHasNoOwner'

_logger = logging.getLogger('dbus.bus')


class NameOwnerWatch(object):
    __slots__ = ('_match', '_pending_call')

    def __init__(self, bus_conn, bus_name, callback):
        validate_bus_name(bus_name)

        def signal_cb(owned, old_owner, new_owner):
            callback(new_owner)

        def error_cb(e):
            if e.get_dbus_name() == _NAME_HAS_NO_OWNER:
                callback('')
            else:
                logging.basicConfig()
                _logger.debug('GetNameOwner(%s) failed:', bus_name,
                              exc_info=(e.__class__, e, None))

        self._match = bus_conn.add_signal_receiver(signal_cb,
                                                   'NameOwnerChanged',
                                                   BUS_DAEMON_IFACE,
                                                   BUS_DAEMON_NAME,
                                                   BUS_DAEMON_PATH)
        self._pending_call = bus_conn.call_async(BUS_DAEMON_NAME,
                                                 BUS_DAEMON_PATH,
                                                 BUS_DAEMON_IFACE,
                                                 'GetNameOwner',
                                                 's', (bus_name,),
                                                 callback, error_cb,
                                                 utf8_strings=True)

    def cancel(self):
        if self._match is not None:
            self._match.remove()
        if self._pending_call is not None:
            self._pending_call.cancel()
        self._match = None
        self._pending_call = None


class BusConnection(Connection):
    """A connection to a D-Bus daemon that implements the
    ``org.freedesktop.DBus`` pseudo-service.
    """

    TYPE_SESSION    = BUS_SESSION
    """Represents a session bus (same as the global dbus.BUS_SESSION)"""

    TYPE_SYSTEM     = BUS_SYSTEM
    """Represents the system bus (same as the global dbus.BUS_SYSTEM)"""

    TYPE_STARTER = BUS_STARTER
    """Represents the bus that started this service by activation (same as
    the global dbus.BUS_STARTER)"""

    START_REPLY_SUCCESS = DBUS_START_REPLY_SUCCESS
    START_REPLY_ALREADY_RUNNING = DBUS_START_REPLY_ALREADY_RUNNING

    def __new__(cls, address_or_type=TYPE_SESSION, mainloop=None):
        bus = cls._new_for_bus(address_or_type, mainloop=mainloop)

        # _bus_names is used by dbus.service.BusName!
        bus._bus_names = weakref.WeakValueDictionary()

        bus._signal_sender_matches = {}
        """Map from SignalMatch to NameOwnerWatch."""

        return bus

    def add_signal_receiver(self, handler_function, signal_name=None,
                            dbus_interface=None, bus_name=None,
                            path=None, **keywords):
        named_service = keywords.pop('named_service', None)
        if named_service is not None:
            if bus_name is not None:
                raise TypeError('bus_name and named_service cannot both be '
                                'specified')
            bus_name = named_service
            from warnings import warn
            warn('Passing the named_service parameter to add_signal_receiver '
                 'by name is deprecated: please use positional parameters',
                 DeprecationWarning, stacklevel=2)

        match = super(BusConnection, self).add_signal_receiver(
                handler_function, signal_name, dbus_interface, bus_name,
                path, **keywords)

        if (bus_name is not None and bus_name != BUS_DAEMON_NAME):
            if bus_name[:1] == ':':
                def callback(new_owner):
                    if new_owner == '':
                        match.remove()
            else:
                callback = match.set_sender_name_owner
            watch = self.watch_name_owner(bus_name, callback)
            self._signal_sender_matches[match] = watch

        self.add_match_string(str(match))

        return match

    def _clean_up_signal_match(self, match):
        # The signals lock is no longer held here (it was in <= 0.81.0)
        self.remove_match_string(str(match))
        watch = self._signal_sender_matches.pop(match, None)
        if watch is not None:
            watch.cancel()

    def activate_name_owner(self, bus_name):
        if (bus_name is not None and bus_name[:1] != ':'
            and bus_name != BUS_DAEMON_NAME):
            try:
                return self.get_name_owner(bus_name)
            except DBusException, e:
                if e.get_dbus_name() != _NAME_HAS_NO_OWNER:
                    raise
                # else it doesn't exist: try to start it
                self.start_service_by_name(bus_name)
                return self.get_name_owner(bus_name)
        else:
            # already unique
            return bus_name

    def get_object(self, bus_name, object_path, introspect=True,
                   follow_name_owner_changes=False, **kwargs):
        """Return a local proxy for the given remote object.

        Method calls on the proxy are translated into method calls on the
        remote object.

        :Parameters:
            `bus_name` : str
                A bus name (either the unique name or a well-known name)
                of the application owning the object. The keyword argument
                named_service is a deprecated alias for this.
            `object_path` : str
                The object path of the desired object
            `introspect` : bool
                If true (default), attempt to introspect the remote
                object to find out supported methods and their signatures
            `follow_name_owner_changes` : bool
                If the object path is a well-known name and this parameter
                is false (default), resolve the well-known name to the unique
                name of its current owner and bind to that instead; if the
                ownership of the well-known name changes in future,
                keep communicating with the original owner.
                This is necessary if the D-Bus API used is stateful.

                If the object path is a well-known name and this parameter
                is true, whenever the well-known name changes ownership in
                future, bind to the new owner, if any.

                If the given object path is a unique name, this parameter
                has no effect.

        :Returns: a `dbus.proxies.ProxyObject`
        :Raises `DBusException`: if resolving the well-known name to a
            unique name fails
        """
        if follow_name_owner_changes:
            self._require_main_loop()   # we don't get the signals otherwise

        named_service = kwargs.pop('named_service', None)
        if named_service is not None:
            if bus_name is not None:
                raise TypeError('bus_name and named_service cannot both '
                                'be specified')
            from warnings import warn
            warn('Passing the named_service parameter to get_object by name '
                 'is deprecated: please use positional parameters',
                 DeprecationWarning, stacklevel=2)
            bus_name = named_service
        if kwargs:
            raise TypeError('get_object does not take these keyword '
                            'arguments: %s' % ', '.join(kwargs.iterkeys()))

        return self.ProxyObjectClass(self, bus_name, object_path,
                                     introspect=introspect,
                                     follow_name_owner_changes=follow_name_owner_changes)

    def get_unix_user(self, bus_name):
        """Get the numeric uid of the process owning the given bus name.

        :Parameters:
            `bus_name` : str
                A bus name, either unique or well-known
        :Returns: a `dbus.UInt32`
        """
        validate_bus_name(bus_name)
        return self.call_blocking(BUS_DAEMON_NAME, BUS_DAEMON_PATH,
                                  BUS_DAEMON_IFACE, 'GetConnectionUnixUser',
                                  's', (bus_name,))

    def start_service_by_name(self, bus_name, flags=0):
        """Start a service which will implement the given bus name on this Bus.

        :Parameters:
            `bus_name` : str
                The well-known bus name to be activated.
            `flags` : dbus.UInt32
                Flags to pass to StartServiceByName (currently none are
                defined)

        :Returns: A tuple of 2 elements. The first is always True, the
            second is either START_REPLY_SUCCESS or
            START_REPLY_ALREADY_RUNNING.

        :Raises DBusException: if the service could not be started.
        """
        validate_bus_name(bus_name)
        return (True, self.call_blocking(BUS_DAEMON_NAME, BUS_DAEMON_PATH,
                                         BUS_DAEMON_IFACE,
                                         'StartServiceByName',
                                         'su', (bus_name, flags)))

    # XXX: it might be nice to signal IN_QUEUE, EXISTS by exception,
    # but this would not be backwards-compatible
    def request_name(self, name, flags=0):
        """Request a bus name.

        :Parameters:
            `name` : str
                The well-known name to be requested
            `flags` : dbus.UInt32
                A bitwise-OR of 0 or more of the flags
                `DBUS_NAME_FLAG_ALLOW_REPLACEMENT`,
                `DBUS_NAME_FLAG_REPLACE_EXISTING`
                and `DBUS_NAME_FLAG_DO_NOT_QUEUE`
        :Returns: `DBUS_REQUEST_NAME_REPLY_PRIMARY_OWNER`,
            `DBUS_REQUEST_NAME_REPLY_IN_QUEUE`,
            `DBUS_REQUEST_NAME_REPLY_EXISTS` or
            `DBUS_REQUEST_NAME_REPLY_ALREADY_OWNER`
        :Raises DBusException: if the bus daemon cannot be contacted or
            returns an error.
        """
        validate_bus_name(name, allow_unique=False)
        return self.call_blocking(BUS_DAEMON_NAME, BUS_DAEMON_PATH,
                                  BUS_DAEMON_IFACE, 'RequestName',
                                  'su', (name, flags))

    def release_name(self, name):
        """Release a bus name.

        :Parameters:
            `name` : str
                The well-known name to be released
        :Returns: `DBUS_RELEASE_NAME_REPLY_RELEASED`,
            `DBUS_RELEASE_NAME_REPLY_NON_EXISTENT`
            or `DBUS_RELEASE_NAME_REPLY_NOT_OWNER`
        :Raises DBusException: if the bus daemon cannot be contacted or
            returns an error.
        """
        validate_bus_name(name, allow_unique=False)
        return self.call_blocking(BUS_DAEMON_NAME, BUS_DAEMON_PATH,
                                  BUS_DAEMON_IFACE, 'ReleaseName',
                                  's', (name,))

    def list_names(self):
        """Return a list of all currently-owned names on the bus.

        :Returns: a dbus.Array of dbus.UTF8String
        """
        return self.call_blocking(BUS_DAEMON_NAME, BUS_DAEMON_PATH,
                                  BUS_DAEMON_IFACE, 'ListNames',
                                  '', (), utf8_strings=True)

    def list_activatable_names(self):
        """Return a list of all names that can be activated on the bus.

        :Returns: a dbus.Array of dbus.UTF8String
        """
        return self.call_blocking(BUS_DAEMON_NAME, BUS_DAEMON_PATH,
                                  BUS_DAEMON_IFACE, 'ListNames',
                                  '', (), utf8_strings=True)

    def get_name_owner(self, bus_name):
        """Return the unique connection name of the primary owner of the
        given name.

        :Raises DBusException: if the `bus_name` has no owner
        """
        validate_bus_name(bus_name, allow_unique=False)
        return self.call_blocking(BUS_DAEMON_NAME, BUS_DAEMON_PATH,
                                  BUS_DAEMON_IFACE, 'GetNameOwner',
                                  's', (bus_name,), utf8_strings=True)

    def watch_name_owner(self, bus_name, callback):
        """Watch the unique connection name of the primary owner of the
        given name.

        `callback` will be called with one argument, which is either the
        unique connection name, or the empty string (meaning the name is
        not owned).
        """
        return NameOwnerWatch(self, bus_name, callback)

    def name_has_owner(self, bus_name):
        """Return True iff the given bus name has an owner on this bus.

        :Parameters:
            `name` : str
                The bus name to look up
        :Returns: a `bool`
        """
        return bool(self.call_blocking(BUS_DAEMON_NAME, BUS_DAEMON_PATH,
                                       BUS_DAEMON_IFACE, 'NameHasOwner',
                                       's', (bus_name,)))

    def add_match_string(self, rule):
        """Arrange for this application to receive messages on the bus that
        match the given rule. This version will block.

        :Parameters:
            `rule` : str
                The match rule
        :Raises: `DBusException` on error.
        """
        self.call_blocking(BUS_DAEMON_NAME, BUS_DAEMON_PATH,
                           BUS_DAEMON_IFACE, 'AddMatch', 's', (rule,))

    # FIXME: add an async success/error handler capability?
    # (and the same for remove_...)
    def add_match_string_non_blocking(self, rule):
        """Arrange for this application to receive messages on the bus that
        match the given rule. This version will not block, but any errors
        will be ignored.


        :Parameters:
            `rule` : str
                The match rule
        :Raises: `DBusException` on error.
        """
        self.call_async(BUS_DAEMON_NAME, BUS_DAEMON_PATH,
                        BUS_DAEMON_IFACE, 'AddMatch', 's', (rule,),
                        None, None)

    def remove_match_string(self, rule):
        """Arrange for this application to receive messages on the bus that
        match the given rule. This version will block.

        :Parameters:
            `rule` : str
                The match rule
        :Raises: `DBusException` on error.
        """
        self.call_blocking(BUS_DAEMON_NAME, BUS_DAEMON_PATH,
                           BUS_DAEMON_IFACE, 'RemoveMatch', 's', (rule,))

    def remove_match_string_non_blocking(self, rule):
        """Arrange for this application to receive messages on the bus that
        match the given rule. This version will not block, but any errors
        will be ignored.


        :Parameters:
            `rule` : str
                The match rule
        :Raises: `DBusException` on error.
        """
        self.call_async(BUS_DAEMON_NAME, BUS_DAEMON_PATH,
                        BUS_DAEMON_IFACE, 'RemoveMatch', 's', (rule,),
                        None, None)
