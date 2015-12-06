#!/bin/sh
# with-session-bus.sh - run a program with a temporary D-Bus session daemon
#
# Copyright (C) 2007-2008 Collabora Ltd. <http://www.collabora.co.uk/>
#
# Copying and distribution of this file, with or without modification,
# are permitted in any medium without royalty provided the copyright
# notice and this notice are preserved.

set -e

me=with-session-bus

dbus_daemon_args="--print-address=5 --print-pid=6 --fork"
sleep=0

usage ()
{
  echo "usage: $me [options] -- program [program_options]" >&2
  echo "Requires write access to the current directory." >&2
  echo "" >&2
  echo "If \$WITH_SESSION_BUS_FORK_DBUS_MONITOR is set, fork dbus-monitor" >&2
  echo "with the arguments in \$WITH_SESSION_BUS_FORK_DBUS_MONITOR_OPT." >&2
  echo "The output of dbus-monitor is saved in $me-<pid>.dbus-monitor-logs" >&2
  exit 2
}

while test "z$1" != "z--"; do
  case "$1" in
  --sleep=*)
    sleep="$1"
    sleep="${sleep#--sleep=}"
    shift
    ;;
  --session)
    dbus_daemon_args="$dbus_daemon_args --session"
    shift
    ;;
  --config-file=*)
    # FIXME: assumes config file doesn't contain any special characters
    dbus_daemon_args="$dbus_daemon_args $1"
    shift
    ;;
  *)
    usage
    ;;
  esac
done
shift
if test "z$1" = "z"; then usage; fi

exec 5> $me-$$.address
exec 6> $me-$$.pid

cleanup ()
{
  pid=`head -n1 $me-$$.pid`
  if test -n "$pid" ; then
    if [ -n "$VERBOSE_TESTS" ]; then
      echo "Killing temporary bus daemon: $pid" >&2
    fi
    kill -INT "$pid"
  fi
  rm -f $me-$$.address
  rm -f $me-$$.pid
}

trap cleanup INT HUP TERM
dbus-daemon $dbus_daemon_args

if [ -n "$VERBOSE_TESTS" ]; then
  { echo -n "Temporary bus daemon is "; cat $me-$$.address; } >&2
  { echo -n "Temporary bus daemon PID is "; head -n1 $me-$$.pid; } >&2
fi

e=0

# These might be non-null when run from e.g. gnome-terminal 3.8, which uses
# an activatable service for its windows; we don't want to inherit them either
unset DBUS_STARTER_ADDRESS
unset DBUS_STARTER_BUS_TYPE

DBUS_SESSION_BUS_ADDRESS="`cat $me-$$.address`"
export DBUS_SESSION_BUS_ADDRESS
DBUS_SESSION_BUS_PID="`cat $me-$$.pid`"
export DBUS_SESSION_BUS_PID

if [ -n "$WITH_SESSION_BUS_FORK_DBUS_MONITOR" ] ; then
  echo -n "Forking dbus-monitor $WITH_SESSION_BUS_FORK_DBUS_MONITOR_OPT" >&2
  dbus-monitor $WITH_SESSION_BUS_FORK_DBUS_MONITOR_OPT \
        > $me-$$.dbus-monitor-logs 2>&1 &
fi

"$@" || e=$?

if test $sleep != 0; then
  sleep $sleep
fi

trap - INT HUP TERM
cleanup

exit $e
