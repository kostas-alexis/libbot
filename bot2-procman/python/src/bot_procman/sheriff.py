"""@package sheriff

\defgroup python_api Python API
@{
"""
import os
import platform
import sys
import time
import random
#import types
import signal

import gobject

import lcm
from bot_procman.info_t import info_t
from bot_procman.orders_t import orders_t
from bot_procman.sheriff_cmd_t import sheriff_cmd_t
from bot_procman.command2_t import command2_t
from bot_procman.info2_t import info2_t
from bot_procman.orders2_t import orders2_t
from bot_procman.sheriff_cmd2_t import sheriff_cmd2_t
from bot_procman.deputy_cmd2_t import deputy_cmd2_t
import bot_procman.sheriff_config as sheriff_config
from bot_procman.sheriff_script import SheriffScript

def _dbg(text):
    return
    #sys.stderr.write("%s\n" % text)

def _warn(text):
    sys.stderr.write("[WARNING] %s\n" % text)

def _now_utime():
    return int(time.time() * 1000000)

TRYING_TO_START = "Command Sent"
RUNNING = "Running"
TRYING_TO_STOP = "Command Sent"
REMOVING = "Command Sent"
STOPPED_OK = "Stopped (OK)"
STOPPED_ERROR = "Stopped (Error)"
UNKNOWN = "Unknown"
RESTARTING = "Command Sent"

DEFAULT_STOP_SIGNAL = 2
DEFAULT_STOP_TIME_ALLOWED = 7

class SheriffCommandSpec(object):
    __slots__ = [ "deputy_name", "exec_str", "command_id", "group_name",
            "auto_respawn", "stop_signal", "stop_time_allowed" ]

    def __init__(self):

        ## the name of the deputy that will manage this command.
        self.deputy_name = ""

        ## the actual command string to execute.
        self.exec_str = ""

        ## an identifier string for this command.  Must be unique within a deputy.
        self.command_id = ""

        ## the command group name, or the empty string for no group.
        self.group_name = ""

        ## True if the deputy should automatically restart the
        # command when it exits.  Auto respawning only happens when the desired
        # state of the command is running.
        self.auto_respawn = False

        ## When stopping the command, this OS-level signal will be sent to the
        # command to request a clean exit.  The default is SIGINT
        self.stop_signal = DEFAULT_STOP_SIGNAL

        ## When stopping the command, the deputy will wait this amount of time
        # (seconds) in between requesting a clean exit and forcing the command
        # to stop via a SIGKILL
        self.stop_time_allowed = DEFAULT_STOP_TIME_ALLOWED


class SheriffDeputyCommand(gobject.GObject):
    """A command managed by a deputy, which is in turn managed by the %Sheriff.

    Represents an executable command that can be run as a child process of a
    Procman deputy process.

    Use this class to  TODO
    """
    def __init__(self):
        gobject.GObject.__init__(self)
        self.sheriff_id = 0
        self.pid = -1
        self.exit_code = 0
        self.cpu_usage = 0
        self.mem_vsize_bytes = 0
        self.mem_rss_bytes = 0
        self.exec_str = ""
        self.command_id = ""
        self.group = ""
        self.desired_runid = 0
        self.force_quit = 0
        self.scheduled_for_removal = False
        self.actual_runid = 0
        self.auto_respawn = False
        self.stop_signal = DEFAULT_STOP_SIGNAL
        self.stop_time_allowed = DEFAULT_STOP_TIME_ALLOWED
        self.updated_from_info = False

    def update_from_cmd_info2(self, cmd_msg):
        self.pid = cmd_msg.pid
        self.actual_runid = cmd_msg.actual_runid
        self.exit_code = cmd_msg.exit_code
        self.cpu_usage = cmd_msg.cpu_usage
        self.mem_vsize_bytes = cmd_msg.mem_vsize_bytes
        self.mem_rss_bytes = cmd_msg.mem_rss_bytes
        self.updated_from_info = True

        # if the command has run to completion and we don't need it to respawn,
        # then prevent it from respawning if the deputy restarts
        if self.pid == 0 and \
            self.actual_runid == self.desired_runid and \
            not self.auto_respawn and \
            not self.force_quit:
                self.force_quit = 1

    def update_from_cmd_order2(self, cmd_msg):
        assert self.sheriff_id == cmd_msg.sheriff_id
        self.exec_str = cmd_msg.cmd.exec_str
        self.command_id = cmd_msg.cmd.command_name
        self.group = cmd_msg.cmd.group
        self.desired_runid = cmd_msg.desired_runid
        self.force_quit = cmd_msg.force_quit
        self.stop_signal = cmd_msg.cmd.stop_signal
        self.stop_time_allowed = cmd_msg.cmd.stop_time_allowed

    def set_group(self, group):
        self.group = group

    def start(self):
        # if the command is already running, then ignore
        if self.pid > 0 and not self.force_quit:
            return

        self.desired_runid += 1
        if self.desired_runid > (2 << 31):
            self.desired_runid = 1
        self.force_quit = 0

    def restart(self):
        self.desired_runid += 1
        if self.desired_runid > (2 << 31):
            self.desired_runid = 1
        self.force_quit = 0

    def stop(self):
        self.force_quit = 1

    def get_group_name(self):
        return self.group

    def status(self):
        if not self.updated_from_info:
            return UNKNOWN
        if self.desired_runid != self.actual_runid and not self.force_quit:
            if self.pid == 0:
                return TRYING_TO_START
            else:
                return RESTARTING
        elif self.desired_runid == self.actual_runid:
            if self.pid > 0:
                if not self.force_quit and not self.scheduled_for_removal:
                    return RUNNING
                else:
                    return TRYING_TO_STOP
            else:
                if self.scheduled_for_removal:
                    return REMOVING
                elif self.exit_code == 0:
                    return STOPPED_OK
                elif self.force_quit and \
                     os.WIFSIGNALED(self.exit_code) and \
                     os.WTERMSIG(self.exit_code) in [ signal.SIGTERM,
                             signal.SIGINT, signal.SIGKILL ]:
                         return STOPPED_OK
                else:                          return STOPPED_ERROR
        else: return UNKNOWN

    def __str__(self):
        return """[%(exec_str)s]
   group:        %(group)s
   sheriff_id:   %(sheriff_id)d
   pid:          %(pid)d
   exit_code:    %(exit_code)d
   cpu_usage:    %(cpu_usage)f
   mem_vsize:    %(mem_vsize_bytes)d
   mem_rss:      %(mem_rss_bytes)d
   actual_runid: %(actual_runid)d""" % self.__dict__

class SheriffDeputy(gobject.GObject):
    """%Sheriff view of a deputy

    You shouldn't need to use this class directly.
    """
    def __init__(self, name):
        gobject.GObject.__init__(self)
        self.name = name
        self.commands = {}
        self.last_update_utime = 0
        self.cpu_load = 0
        self.phys_mem_total_bytes = 0
        self.phys_mem_free_bytes = 0
        self.variables = {}
        self._orders_version = 2

    def get_commands(self):
        """Retrieve a list of all commands managed by the deputy

        @return a list of SheriffDeputyCommand objects
        """
        return self.commands.values()

    def owns_command(self, command):
        """Check to see if this deputy manages the specified command

        @param command a SheriffDeputyCommand object.

        @return True if this deputy object manages \p command, False if not.
        """
        return command.sheriff_id in self.commands and \
                self.commands [command.sheriff_id] is command

    def get_variables(self):
        return self.variables

    def set_variable(self, name, val):
        self.variables[name] = val

    def remove_variable(self, name):
        if name in self.variables:
            del self.variables[name]

    def get_variable(self, name):
        return self.variables[name]

    def update_from_deputy_info2(self, dep_info_msg):
        """
        @dep_info_msg: an instance of bot_procman.info2_t
        """
        status_changes = []
        for cmd_msg in dep_info_msg.cmds:
            # look up the command, or create a new one if it's not found
            if cmd_msg.sheriff_id in self.commands:
                cmd = self.commands[cmd_msg.sheriff_id]
                old_status = cmd.status()
            else:
                cmd = SheriffDeputyCommand()
                cmd.exec_str = cmd_msg.cmd.exec_str
                cmd.command_id = cmd_msg.cmd.command_name
                cmd.group = cmd_msg.cmd.group
                cmd.auto_respawn = cmd_msg.cmd.auto_respawn
                cmd.stop_signal = cmd_msg.cmd.stop_signal
                cmd.sheriff_id = cmd_msg.sheriff_id
                cmd.desired_runid = cmd_msg.actual_runid
                cmd.stop_time_allowed = cmd_msg.cmd.stop_time_allowed
                # TODO handle options
                self.add_command(cmd)
                old_status = None

            cmd.update_from_cmd_info2(cmd_msg)
            new_status = cmd.status()

            if old_status != new_status:
                status_changes.append((cmd, old_status, new_status))

        updated_ids = [ cmd_msg.sheriff_id for cmd_msg in dep_info_msg.cmds ]

        can_safely_remove = [ cmd for cmd in self.commands.values() \
                if cmd.scheduled_for_removal and \
                cmd.sheriff_id not in updated_ids ]

        for toremove in can_safely_remove:
            cmd = self.commands[toremove.sheriff_id]
            old_status = cmd.status()
            status_changes.append((cmd, old_status, None))
            del self.commands[toremove.sheriff_id]

        # TODO update variables

        self.last_update_utime = _now_utime()
        self.cpu_load = dep_info_msg.cpu_load
        self.phys_mem_total_bytes = dep_info_msg.phys_mem_total_bytes
        self.phys_mem_free_bytes = dep_info_msg.phys_mem_free_bytes
        return status_changes

    def update_from_deputy_orders2(self, orders_msg):
        status_changes = []
        for cmd_msg in orders_msg.cmds:
            if cmd_msg.sheriff_id in self.commands:
                cmd = self.commands[cmd_msg.sheriff_id]
                old_status = cmd.status()
            else:
                cmd = SheriffDeputyCommand()
                cmd.sheriff_id = cmd_msg.sheriff_id
                cmd.exec_str = cmd_msg.cmd.exec_str
                cmd.command_id = cmd_msg.cmd.command_name
                cmd.group = cmd_msg.cmd.group
                cmd.auto_respawn = cmd_msg.cmd.auto_respawn
                cmd.stop_signal = cmd_msg.cmd.stop_signal
                cmd.stop_time_allowed = cmd_msg.cmd.stop_time_allowed
                cmd.desired_runid = cmd_msg.desired_runid
                self.add_command(cmd)
                old_status = None
            cmd.update_from_cmd_order2(cmd_msg)
            new_status = cmd.status()
            if old_status != new_status:
                status_changes.append((cmd, old_status, new_status))
        updated_ids = set([ cmd_msg.sheriff_id for cmd_msg in orders_msg.cmds ])
        for cmd in self.commands.values():
            if cmd.sheriff_id not in updated_ids:
                old_status = cmd.status()
                cmd.scheduled_for_removal = True
                new_status = cmd.status()
                if old_status != new_status:
                    status_changes.append((cmd, old_status, new_status))
        # TODO update variables
        return status_changes

    def set_orders_version(self, version):
        self._orders_version = version

    def get_orders_version(self):
        return self._orders_version

    def add_command(self, newcmd):
        assert newcmd.sheriff_id != 0
        assert isinstance(newcmd, SheriffDeputyCommand)
        self.commands[newcmd.sheriff_id] = newcmd

    def schedule_for_removal(self, cmd):
        if not self.owns_command(cmd):
            raise KeyError("invalid command")
        old_status = cmd.status()
        cmd.scheduled_for_removal = True
        if not self.last_update_utime:
            del self.commands[cmd.sheriff_id]
            new_status = None
        else:
            new_status = cmd.status()
        return ((cmd, old_status, new_status),)

    def _make_orders_message(self, sheriff_name):
        orders = orders_t()
        orders.utime = _now_utime()
        orders.host = self.name
        orders.ncmds = len(self.commands)
        orders.sheriff_name = sheriff_name
        for cmd in self.commands.values():
            if cmd.scheduled_for_removal:
                orders.ncmds -= 1
                continue
            cmd_msg = sheriff_cmd_t()
            cmd_msg.name = cmd.exec_str
            cmd_msg.nickname = cmd.command_id
            cmd_msg.sheriff_id = cmd.sheriff_id
            cmd_msg.desired_runid = cmd.desired_runid
            cmd_msg.force_quit = cmd.force_quit
            cmd_msg.group = cmd.group
            cmd_msg.auto_respawn = cmd.auto_respawn
            orders.cmds.append(cmd_msg)
        orders.nvars = len(self.variables)
        for name, val in self.variables.items():
            orders.varnames.append(name)
            orders.varvals.append(val)
        return orders

    def _make_orders2_message(self, sheriff_name):
        msg = orders2_t()
        msg.utime = _now_utime()
        msg.host = self.name
        msg.ncmds = len(self.commands)
        msg.sheriff_name = sheriff_name
        for cmd in self.commands.values():
            if cmd.scheduled_for_removal:
                msg.ncmds -= 1
                continue
            cmd_msg = sheriff_cmd2_t()
            cmd_msg.cmd = command2_t()
            cmd_msg.cmd.exec_str = cmd.exec_str
            cmd_msg.cmd.command_name = cmd.command_id
            cmd_msg.cmd.group = cmd.group
            cmd_msg.cmd.auto_respawn = cmd.auto_respawn
            cmd_msg.cmd.stop_signal = cmd.stop_signal
            cmd_msg.cmd.stop_time_allowed = cmd.stop_time_allowed
            cmd_msg.cmd.num_options = 0
            cmd_msg.cmd.option_names = []
            cmd_msg.cmd.option_values = []
            cmd_msg.sheriff_id = cmd.sheriff_id
            cmd_msg.desired_runid = cmd.desired_runid
            cmd_msg.force_quit = cmd.force_quit
            msg.cmds.append(cmd_msg)
        msg.num_options = 0
        msg.option_names = []
        msg.option_values = []
        return msg

class ScriptExecutionContext(object):
    def __init__(self, sheriff, script):
        assert(script is not None)
        self.script = script
        self.current_action = -1
        self.subscript_context = None
        self.sheriff = sheriff

    def get_next_action(self):
        if self.subscript_context:
            # if we're recursing into a called script, return its next action
            action = self.subscript_context.get_next_action()
            if action:
                return action
            else:
                # unless it's done, in which case fall through to our next
                # action
                self.subscript_context = None
        self.current_action += 1
        if self.current_action >= len(self.script.actions):
            # no more actions
            return None
        action = self.script.actions[self.current_action]

        if action.action_type == "run_script":
            subscript = self.sheriff.get_script(action.script_name)
            self.subscript_context = ScriptExecutionContext(self.sheriff,
                    subscript)
            return self.get_next_action()
        else:
            return action

class Sheriff(gobject.GObject):
    """Controls deputies and processes.

    The Sheriff class provides the primary interface for controlling processes
    using the Procman Python API.  It requires a GLib event loop to run.

    example usage:
    \code
    import bot_procman
    import gobject

    lc = lcm.LCM()
    sheriff = bot_procman.Sheriff(lc)

    # add commands or load a config file

    mainloop = gobject.MainLoop()
    gobject.io_add_watch(lc, gobject.IO_IN, lambda *s: lc.handle() or True)
    gobject.timeout_add(1000, lambda *s: sheriff.send_orders() or True)
    mainloop.run()
    \endcode
    """

    __gsignals__ = {
            'deputy-info-received' : (gobject.SIGNAL_RUN_LAST,
                gobject.TYPE_NONE, (gobject.TYPE_PYOBJECT,)),
            'command-added' : (gobject.SIGNAL_RUN_LAST,
                gobject.TYPE_NONE,
                (gobject.TYPE_PYOBJECT, gobject.TYPE_PYOBJECT)),
            'command-removed' : (gobject.SIGNAL_RUN_LAST,
                gobject.TYPE_NONE,
                (gobject.TYPE_PYOBJECT, gobject.TYPE_PYOBJECT)),
            'command-status-changed' : (gobject.SIGNAL_RUN_LAST,
                gobject.TYPE_NONE,
                (gobject.TYPE_PYOBJECT, gobject.TYPE_PYOBJECT,
                    gobject.TYPE_PYOBJECT)),
            'command-group-changed' : (gobject.SIGNAL_RUN_LAST,
                gobject.TYPE_NONE, (gobject.TYPE_PYOBJECT,)),
            'script-added' : (gobject.SIGNAL_RUN_LAST,
                gobject.TYPE_NONE, (gobject.TYPE_PYOBJECT,)),
            'script-removed' : (gobject.SIGNAL_RUN_LAST,
                gobject.TYPE_NONE, (gobject.TYPE_PYOBJECT,)),
            'script-started' : (gobject.SIGNAL_RUN_LAST,
                gobject.TYPE_NONE, (gobject.TYPE_PYOBJECT,)),
            'script-action-executing' : (gobject.SIGNAL_RUN_LAST,
                gobject.TYPE_NONE, (gobject.TYPE_PYOBJECT,
                    gobject.TYPE_PYOBJECT,)),
            'script-finished' : (gobject.SIGNAL_RUN_LAST,
                gobject.TYPE_NONE, (gobject.TYPE_PYOBJECT,))
            }

    def __init__ (self, comms):
        gobject.GObject.__init__ (self)
        self.comms = comms
        self.comms.subscribe("PMD_INFO", self._on_pmd_info)
        self.comms.subscribe("PMD_INFO2", self._on_pmd_info2)
        self.comms.subscribe("PMD_ORDERS", self._on_pmd_orders)
        self.comms.subscribe("PMD_ORDERS2", self._on_pmd_orders2)
        self.deputies = {}
        self._is_observer = False
        self.name = platform.node() + ":" + str(os.getpid()) + \
                ":" + str(_now_utime())

        # variables for scripts
        self.scripts = []
        self.active_script_context = None
        self.waiting_on_commands = []
        self.waiting_for_status = None
        self.last_script_action_time = None

    def _get_or_make_deputy(self, deputy_name):
        if deputy_name not in self.deputies:
            self.deputies[deputy_name] = SheriffDeputy(deputy_name)
        return self.deputies[deputy_name]

    def _maybe_emit_status_change_signals(self, deputy, status_changes):
        for cmd, old_status, new_status in status_changes:
            if old_status == new_status:
                continue
            if old_status is None:
                self.emit("command-added", deputy, cmd)
            elif new_status is None:
                self.emit("command-removed", deputy, cmd)
            else:
                self._check_wait_action_status()
                self.emit("command-status-changed", cmd,
                        old_status, new_status)

    def _get_command_deputy(self, cmd):
        for deputy in self.deputies.values():
            if deputy.owns_command(cmd):
                return deputy
        raise KeyError()

    def _handle_info2_t(self, info_msg, version):
        now = _now_utime()
        if(now - info_msg.utime) * 1e-6 > 30 and not self.is_observer:
            # ignore old messages
            return

        _dbg("received pmd info from [%s]" % info_msg.host)

        deputy = self._get_or_make_deputy(info_msg.host)

        # If this is the first time we've heard from the deputy and we already
        # have a desired state for the deputy, then try to reconcile the stored
        # desired state with the deputy's reported state.
        if not deputy.last_update_utime and deputy.commands:
            _dbg("First update from [%s]" % info_msg.host)
            # for each command we already have lined up in the deputy, check to
            # see if the deputy is already managing that command.  If the
            # deputy is already managing that command, then reassign the
            # internal ID for the command to match what the deputy is
            # reporting.
            for cmd in deputy.commands.values():
                for cmd_msg in info_msg.cmds:
                    matched = cmd.exec_str == cmd_msg.cmd.exec_str and \
                              cmd.command_id == cmd_msg.cmd.command_name and \
                              cmd.group == cmd_msg.cmd.group and \
                              cmd.auto_respawn == cmd_msg.cmd.auto_respawn
                    if not matched:
                        continue
                    collision = False
                    for other_deputy in self.deputies.values():
                        if other_deputy.commands.get(cmd_msg.sheriff_id, cmd) \
                                is not cmd:
                            collision = True
                            break
                    if collision:
                        continue
                    # found a command managed by the deputy that looks
                    # exactly like the command the sheriff wants the
                    # deputy to run.  Reassign the sheriff ID to match
                    # what the deputy is reporting.
                    del deputy.commands[cmd.sheriff_id]
                    cmd.sheriff_id = cmd_msg.sheriff_id
                    deputy.commands[cmd.sheriff_id] = cmd
                    _dbg("Merging command [%s] with command reported by deputy" \
                            % cmd.command_id)
                    break

        deputy.set_orders_version(version)

        status_changes = deputy.update_from_deputy_info2(info_msg)

        self.emit("deputy-info-received", deputy)
        self._maybe_emit_status_change_signals(deputy, status_changes)

    def _on_pmd_info2(self, _, data):
        try:
            info_msg = info2_t.decode(data)
        except ValueError:
            print("invalid info2_t message")
            return
        self._handle_info2_t(info_msg, 2)

    def _on_pmd_info(self, _, data):
        try:
            dep_info = info_t.decode(data)
        except ValueError:
            print("invalid info_t message")
            return

        new_info_msg = info2_t()
        new_info_msg.utime = dep_info.utime
        new_info_msg.host = dep_info.host
        new_info_msg.cpu_load = dep_info.cpu_load
        new_info_msg.phys_mem_total_bytes = dep_info.phys_mem_total_bytes
        new_info_msg.phys_mem_free_bytes = dep_info.phys_mem_free_bytes
        new_info_msg.swap_total_bytes = dep_info.swap_total_bytes
        new_info_msg.swap_free_bytes = dep_info.swap_free_bytes
        new_info_msg.ncmds = dep_info.ncmds
        new_info_msg.num_options = 0
        new_info_msg.option_names = []
        new_info_msg.option_values = []
        new_info_msg.cmds = []
        for cmd_index, cmd_info in enumerate(dep_info.cmds):
            cmd_msg = dep_info.cmds[cmd_index]
            new_cmd_msg = deputy_cmd2_t()
            new_cmd_msg.cmd = command2_t()
            new_cmd_msg.cmd.exec_str = cmd_msg.name
            new_cmd_msg.cmd.command_name = cmd_msg.nickname
            new_cmd_msg.cmd.group = cmd_msg.group
            new_cmd_msg.cmd.auto_respawn = cmd_msg.auto_respawn
            new_cmd_msg.cmd.stop_signal = DEFAULT_STOP_SIGNAL
            new_cmd_msg.cmd.stop_time_allowed = DEFAULT_STOP_TIME_ALLOWED
            new_cmd_msg.cmd.num_options = 0
            new_cmd_msg.cmd.option_names = []
            new_cmd_msg.cmd.option_values = []
            new_cmd_msg.pid = cmd_msg.pid
            new_cmd_msg.actual_runid = cmd_msg.actual_runid
            new_cmd_msg.exit_code = cmd_msg.exit_code
            new_cmd_msg.cpu_usage = cmd_msg.cpu_usage
            new_cmd_msg.mem_vsize_bytes = cmd_msg.mem_vsize_bytes
            new_cmd_msg.mem_rss_bytes = cmd_msg.mem_rss_bytes
            new_cmd_msg.sheriff_id = cmd_msg.sheriff_id
            new_info_msg.cmds.append(new_cmd_msg)
        self._handle_info2_t(new_info_msg, 1)

    def _handle_orders2_t(self, orders_msg):
        if not self._is_observer:
            return

        deputy = self._get_or_make_deputy(orders_msg.host)
        status_changes = deputy.update_from_deputy_orders2(orders_msg)
        self._maybe_emit_status_change_signals(deputy, status_changes)

    def _on_pmd_orders2(self, _, data):
        orders_msg = orders2_t.decode(data)
        self._handle_orders2_t(orders_msg)

    def _on_pmd_orders(self, _, data):
        dep_orders = orders_t.decode(data)

        new_orders = orders2_t()
        new_orders.utime = dep_orders.utime
        new_orders.host = dep_orders.host
        new_orders.sheriff_name = dep_orders.sheriff_name
        new_orders.num_options = 0
        new_orders.option_names = []
        new_orders.option_values = []
        new_orders.ncmds = dep_orders.ncmds
        for cmd_index, cmd_order in enumerate(dep_orders.cmds):
            cmd_msg = dep_orders.cmds[cmd_index]
            new_cmd_msg = sheriff_cmd2_t()
            new_cmd_msg.cmd = command2_t()
            new_cmd_msg.cmd.exec_str = cmd_msg.name
            new_cmd_msg.cmd.command_name = cmd_msg.nickname
            new_cmd_msg.cmd.group = cmd_msg.group
            new_cmd_msg.cmd.auto_respawn = cmd_msg.auto_respawn
            new_cmd_msg.cmd.stop_signal = DEFAULT_STOP_SIGNAL
            new_cmd_msg.cmd.stop_time_allowed = DEFAULT_STOP_TIME_ALLOWED
            new_cmd_msg.cmd.num_options = 0
            new_cmd_msg.cmd.option_names = []
            new_cmd_msg.cmd.option_values = []
            new_cmd_msg.desired_runid = cmd_msg.desired_runid
            new_cmd_msg.force_quit = cmd_msg.force_quit
            new_cmd_msg.sheriff_id = cmd_msg.sheriff_id
            new_orders.cmds.append(new_cmd_msg)
        self._handle_orders2_t(new_orders)

    def __get_free_sheriff_id(self):
        id_to_try = random.randint(0, (1 << 31) - 1)

        for _ in range(1 << 16):
            collision = False
            for deputy in self.deputies.values():
                if id_to_try in deputy.commands:
                    collision = True
                    break

            if not collision:
                result = id_to_try

            id_to_try = random.randint(0, (1 << 31) - 1)

            if not collision:
                return result
        raise RuntimeError("no available sheriff id")

    def send_orders(self):
        """Transmit orders to all deputies.
        Call this method for the sheriff to send updated orders to its deputies.
        This method is automatically called when you call other sheriff methods
        such as add_command, start_command, etc.  In general, you should only
        need to explicitly call this method for a periodic transmission to be
        robust against network failures and dropped messages.

        @note Orders will only be sent to a deputy if the sheriff has received at
        least one update from the deputy.
        """
        if self._is_observer:
            raise ValueError("Can't send orders in Observer mode")
        for deputy in self.deputies.values():
            # only send orders to a deputy if we've heard from it.
            if deputy.last_update_utime > 0:

                version = deputy.get_orders_version()
                if version == 1:
                    msg = deputy._make_orders_message(self.name)
                    self.comms.publish("PMD_ORDERS", msg.encode())
                else:
                    msg = deputy._make_orders2_message(self.name)
                    self.comms.publish("PMD_ORDERS2", msg.encode())

    def add_command(self, spec):
        """Add a new command.

        @param spec a SheriffCommandSpec that describes the new command to add

        @return a SheriffDeputyCommand object representing the command.
        """
        if self._is_observer:
            raise ValueError("Can't add commands in Observer mode")

        if not spec.exec_str:
            raise ValueError("Invalid command")
        if not spec.command_id:
            raise ValueError("Invalid command id")
        if self.get_commands_by_deputy_and_id(spec.deputy_name, spec.command_id):
            _warn("Duplicate command id %s in group [%s]" % (spec.command_id, spec.group_name))
        if not spec.deputy_name:
            raise ValueError("Invalid deputy")

        dep = self._get_or_make_deputy(spec.deputy_name)
        newcmd = SheriffDeputyCommand()
        newcmd.exec_str = spec.exec_str
        newcmd.command_id = spec.command_id
        newcmd.group = spec.group_name
        print("newcmd.group: %s" % newcmd.group)
        newcmd.sheriff_id = self.__get_free_sheriff_id()
        newcmd.auto_respawn = spec.auto_respawn
        newcmd.stop_signal = spec.stop_signal
        newcmd.stop_time_allowed = spec.stop_time_allowed
        dep.add_command(newcmd)
        self.emit("command-added", dep, newcmd)
        self.send_orders()
        return newcmd

    def start_command(self, cmd):
        """Sets a command's desired status to running.

        If the command is not running, then the deputy will start it.
        If the command is already running, then no action is taken.
        This method calls send_orders().

        @param cmd a SheriffDeputyCommand object specifying the command to run.
        """
        if self._is_observer:
            raise ValueError("Can't modify commands in Observer mode")
        old_status = cmd.status()
        cmd.start()
        new_status = cmd.status()
        deputy = self.get_command_deputy(cmd)
        self._maybe_emit_status_change_signals(deputy,
                ((cmd, old_status, new_status),))
        self.send_orders()

    def restart_command(self, cmd):
        """Starts a command if it's not running, or stop and then start it if it's
        already running.

        If the command is not running, then the deputy will start it.
        If the command is already running, then the deputy will terminate it and
        then start it again.
        This method calls send_orders().

        @param cmd a SheriffDeputyCommand object specifying the command to
        restart.
        """
        if self._is_observer:
            raise ValueError("Can't modify commands in Observer mode")
        old_status = cmd.status()
        cmd.restart()
        new_status = cmd.status()
        deputy = self.get_command_deputy(cmd)
        self._maybe_emit_status_change_signals(deputy,
                ((cmd, old_status, new_status),))
        self.send_orders()

    def stop_command(self, cmd):
        """Sets a command's desired status to stopped.

        If the command is running, then the deputy will stop it.
        If the command is not running, then no action is taken.
        This method calls send_orders().

        @param cmd a SheriffDeputyCommand object specifying the command to stop.
        """
        if self._is_observer:
            raise ValueError("Can't modify commands in Observer mode")
        old_status = cmd.status()
        cmd.stop()
        new_status = cmd.status()
        deputy = self.get_command_deputy(cmd)
        self._maybe_emit_status_change_signals(deputy,
                ((cmd, old_status, new_status),))
        self.send_orders()

    def set_command_exec(self, cmd, exec_str):
        """Set the actual command to be executed when running a command.

        Calling this will not terminate the command if it's already running, and
        the new execution command will not take effect until the next time the
        command is run by the deputy.

        This method does not call send_orders()

        @param cmd a SheriffDeputyCommand object.
        @param exec_str the actual command string to execute.
        """
        cmd.exec_str = exec_str

    def set_command_id(self, cmd, new_id):
        """Set the command id.

        @param cmd a SheriffDeputyCommand object.
        @param new_id the new id to identify a command with.
        """
        if not new_id.strip():
            raise ValueError("Empty command id not allowed")
        if self.get_commands_by_id(new_id):
            _warn("Duplicate command id [%s]" % new_id)
        cmd.command_id = new_id

    def set_command_group(self, cmd, group_name):
        """Set the command group.

        @param cmd a SheriffDeputyCommand object.
        @param group_name the new group name for the command.
        """
        group_name = group_name.strip("/")
        while group_name.find("//") >= 0:
            group_name = group_name.replace("//", "/")
        if self._is_observer:
            raise ValueError("Can't modify commands in Observer mode")
#        deputy = self._get_command_deputy(cmd)
        old_group = cmd.get_group_name()
        if old_group != group_name:
            cmd.set_group(group_name)
            self.emit("command-group-changed", cmd)

    def set_auto_respawn(self, cmd, newauto_respawn):
        """Set if a deputy should auto-respawn the command when the command
        terminates.

        @param cmd a SheriffDeputyCommand object.
        @param newauto_respawn True if the command should be automatically
        restarted.
        """
        cmd.auto_respawn = newauto_respawn

    def schedule_command_for_removal(self, command):
        """Remove a command.

        This starts the process of purging a command from the sheriff and
        deputies.  It is not instantaneous, because the sheriff needs to wait for
        removal confirmation from the deputy.

        This method calls send_orders()

        @param cmd a SheriffDeputyCommand object to remove.
        """
        if self._is_observer:
            raise ValueError("Can't remove commands in Observer mode")
        deputy = self.get_command_deputy(command)
        status_changes = deputy.schedule_for_removal(command)
        self._maybe_emit_status_change_signals(deputy, status_changes)
        self.send_orders()

    def move_command_to_deputy(self, cmd, newdeputy_name):
        """Move a command from one deputy to another.

        This removes the command from one deputy, and creates it in another.
        This method calls send_orders().  On return, the passed in command
        object is no longer valid and should not be used.

        @param cmd a SheriffDeputyCommand object to move.  This object is invalidated by this method.
        @newdeputy_name the name of the new deputy for the command.

        @return the newly created command
        """
        self.schedule_command_for_removal(cmd)
        spec = SheriffCommandSpec()
        spec.deputy_name = newdeputy_name
        spec.exec_str = cmd.exec_str
        spec.command_id = cmd.command_id
        spec.group_name = cmd.group
        spec.auto_respawn = cmd.auto_respawn
        spec.stop_signal = cmd.stop_signal
        spec.stop_time_allowed = cmd.stop_time_allowed
        return self.add_command(spec)

    def set_observer(self, is_observer):
        """Set the sheriff into observation mode, or remove it from observation
        mode.

        @param is_observer True if the sheriff should enter observation mode,
        False if it should leave it.
        """
        self._is_observer = is_observer

    def is_observer(self):
        """Check if the sheriff is in observer mode.

        @return True if the sheriff is in observer mode, False if not.
        """
        return self._is_observer

    def get_deputies(self):
        """Retrieve a list of known deputies.

        @return a list of SheriffDeputy objects.
        """
        return self.deputies.values()

    def find_deputy(self, name):
        """Retrieve the SheriffDeputy object by deputy name.

        @param name the name of the desired deputy.

        @return a SheriffDeputy object.
        """
        return self.deputies[name]

    def purge_useless_deputies(self):
        """Clean up the Sheriff internal state.

        This method is meant to be called when a deputy process has no more
        commands and terminates.  It purges the Sheriff's internal representation
        of deputies that don't have any commands.
        """
        for deputy_name, deputy in self.deputies.items():
            cmds = deputy.commands.values()
            if not deputy.commands or \
                    all([ cmd.scheduled_for_removal for cmd in cmds ]):
                del self.deputies[deputy_name]

    def get_command_by_sheriff_id(self, sheriff_id):
        for deputy in self.deputies.values():
            if sheriff_id in deputy.commands:
                return deputy.commands[sheriff_id]
        raise KeyError("No such command")

    def get_command_deputy(self, command):
        """Retrieve the SheriffDeputy that manages the specified command.

        @param command a SheriffDeputyCommand object

        @return a SheriffDeputy object corresponding to the deputy that manages
        the specified command.
        """
        for deputy in self.deputies.values():
            if command.sheriff_id in deputy.commands:
                return deputy
        raise KeyError("No such command")

    def get_all_commands(self):
        """Retrieve all commands managed by all deputies.

        @return a list of SheriffDeputyCommand objects.
        """
        cmds = []
        for dep in self.deputies.values():
            cmds.extend(dep.commands.values())
        return cmds

    def get_commands_by_deputy_and_id(self, deputy_name, cmd_id):
        """Search for commands with the specified deputy name and command id.
        This should return at most one command.

        @param deputy_name the desired deputy name
        @param cmd_id the desired command id.

        @return a list of SheriffDeputyCommand objects matching the query, or an
        empty list if none are found.
        """
        if deputy_name not in self.deputies:
            return []
        result = []
        for cmd in self.deputies[deputy_name].commands.values():
            if cmd.command_id == cmd_id:
                result.append(cmd)
        return result

    def get_commands_by_id(self, cmd_id):
        """Retrieve all commands with the specified id.
        This should only return one command.

        @param cmd_id the desired command id.

        @return a list of SheriffDeputyCommand objects matching the query, or an
        empty list if none are found.
        """
        result = []
        for deputy in self.deputies.values():
            for cmd in deputy.commands.values():
                if cmd.command_id == cmd_id:
                    result.append(cmd)
        return result

    def get_commands_by_group(self, group_name):
        """Retrieve a list of all commands in the specified group.

        Use this method to find out what commands are in a group.  Commands in
        subgroups of the specified group are also included.

        @param group_name the name of the desired group

        @return a list of SheriffDeputyCommand objects.
        """
        result = []
        group_name = group_name.strip("/")
        while group_name.find("//") >= 0:
            group_name = group_name.replace("//", "/")
        group_parts = group_name.split("/")
        for deputy in self.deputies.values():
            for cmd in deputy.commands.values():
                cmd_group_parts = cmd.group.split("/")
                if len(group_parts) <= len(cmd_group_parts) and \
                        all([ cgp == gp for cgp, gp in zip(group_parts,
                            cmd_group_parts)]):
                    result.append(cmd)
        return result

    def get_active_script(self):
        """Retrieve the currently executing script

        @return the SheriffScript object corresponding to the active script, or
        None if there is no active script.
        """
        if self.active_script_context:
            return self.active_script_context.script
        return None

    def get_script(self, name):
        """Look up a script by name

        @param name the name of the script

        @return a SheriffScript object, or None if no such script is found.
        """
        for script in self.scripts:
            if script.name == name:
                return script
        return None

    def get_scripts(self):
        """Retrieve a list of all scripts

        @return a list of SheriffScript objects
        """
        return self.scripts

    def add_script(self, script):
        """Add a new script to the sheriff.

        @param script a SheriffScript object.
        """
        if self.get_script(script.name) is not None:
            raise ValueError("Script [%s] already exists", script.name)
        self.scripts.append(script)
        self.emit("script-added", script)

    def remove_script(self, script):
        """Remove a script.

        @param script the SheriffScript object to remove.
        """
        if self.active_script_context is not None:
            raise RuntimeError("Script removal is not allowed while a script is running.")

        if script in self.scripts:
            self.scripts.remove(script)
            self.emit("script-removed", script)
        else:
            raise ValueError("Unknown script [%s]", script.name)

    def _get_action_commands(self, ident_type, ident):
        if ident_type == "cmd":
            return self.get_commands_by_id(ident)
        elif ident_type == "group":
            return self.get_commands_by_group(ident)
        elif ident_type == "everything":
            return self.get_all_commands()
        else:
            raise ValueError("Invalid ident_type %s" % ident_type)

    def check_script_for_errors(self, script, path_to_root=None):
        """Check a script object for errors that would prevent its execution

        Possible errors include a command or group mentioned in the script not
        being found by the sheriff.

        @param script a SheriffScript object to inspect

        @return a list of error messages.  If the list is not empty, then each
        error message indicates a problem with the script.  Otherwise, the script
        can be executed.
        """
        if path_to_root is None:
            path_to_root = []
        err_msgs = []
        check_subscripts = True
        if path_to_root and script in path_to_root:
            err_msgs.append("Infinite loop: script %s eventually calls itself" % script.name)
            check_subscripts = False

        for action in script.actions:
            if action.action_type in \
                    [ "start", "stop", "restart", "wait_status" ]:
                if action.ident_type == "cmd":
                    if not self.get_commands_by_id(action.ident):
                        err_msgs.append("No such command: %s" % action.ident)
                elif action.ident_type == "group":
                    if not self.get_commands_by_group(action.ident):
                        err_msgs.append("No such group: %s" % action.ident)
            elif action.action_type == "wait_ms":
                if action.delay_ms < 0:
                    err_msgs.append("Wait times must be nonnegative")
            elif action.action_type == "run_script":
                # action is to run another script.
                subscript = self.get_script(action.script_name)
                if subscript is None:
                    # couldn't find that script.  error out
                    err_msgs.append("Unknown script \"%s\"" % \
                            action.script_name)
                elif check_subscripts:
                    # Recursively check the caleld script for errors.
                    path = path_to_root + [script]
                    sub_messages = self.check_script_for_errors(subscript,
                            path)
                    parstr = "->".join([s.name for s in (path + [subscript])])
                    for msg in sub_messages:
                        err_msgs.append("%s - %s" % (parstr, msg))

            else:
                err_msgs.append("Unrecognized action %s" % action.action_type)
        return err_msgs

    def _finish_script_execution(self):
        script = self.active_script_context.script
        self.active_script_context = None
        self.waiting_on_commands = []
        self.waiting_for_status = None
        if script:
            self.emit("script-finished", script)

    def _check_wait_action_status(self):
        if not self.waiting_on_commands:
            return

        # hack.. don't execute actions faster than 10 Hz
        time_elapsed_ms = (_now_utime() - self.last_script_action_time) * 1000
        if time_elapsed_ms < 100:
            return

        if self.waiting_for_status == "running":
            acceptable_statuses = RUNNING
        elif self.waiting_for_status == "stopped":
            acceptable_statuses = [ STOPPED_OK, STOPPED_ERROR ]
        else:
            raise ValueError("Invalid desired status %s" % \
                    self.waiting_for_status)

        for cmd in self.waiting_on_commands:
            if cmd.status() not in acceptable_statuses:
                return

        # all commands passed the status check.  schedule the next action
        self.waiting_on_commands = []
        self.waiting_for_status = None
        gobject.timeout_add(0, self._execute_next_script_action)

    def _execute_next_script_action(self):
        # make sure there's an active script
        if not self.active_script_context:
            return False

        action = self.active_script_context.get_next_action()

        if action is None:
            # no more actions, script is done.
            self._finish_script_execution()
            return False

        assert action.action_type != "run_script"

        self.emit("script-action-executing", self.active_script_context.script,
                action)

        # fixed time wait -- just set a GObject timer to call this function
        # again
        if action.action_type == "wait_ms":
            gobject.timeout_add(action.delay_ms,
                    self._execute_next_script_action)
            return False

        # find the commands that we're operating on
        cmds = self._get_action_commands(action.ident_type, action.ident)

        self.last_script_action_time = _now_utime()

        # execute an immediate action if applicable
        if action.action_type == "start":
            for cmd in cmds:
                self.start_command(cmd)
        elif action.action_type == "stop":
            for cmd in cmds:
                self.stop_command(cmd)
        elif action.action_type == "restart":
            for cmd in cmds:
                self.restart_command(cmd)

        # do we need to wait for the commands to achieve a desired status?
        if action.wait_status:
            # yes
            self.waiting_on_commands = cmds
            self.waiting_for_status = action.wait_status
            self._check_wait_action_status()
        else:
            # no.  Just move on
            gobject.timeout_add(0, self._execute_next_script_action)

        return False

    def execute_script(self, script):
        """Starts executing a script

        If another script is executing, then that script is aborted first.
        Calling this method executes the first action in the script.  Other
        actions will be invoked during LCM message handling and by the GLib event
        loop (e.g., timers).

        @param script a sheriff_script.SheriffScript object to execute
        @sa get_script()

        @return a list of error messages.  If the list is not empty, then each
        error message indicates a problem with the script.  Otherwise, the script
        has successfully started execution if the returned list is empty.
        """
        if self.active_script_context:
            self.abort_script()

        errors = self.check_script_for_errors(script)
        if errors:
            return errors

        self.active_script_context = ScriptExecutionContext(self, script)
        self.emit("script-started", script)
        self._execute_next_script_action()

    def abort_script(self):
        """Cancels execution of the active script."""
        self._finish_script_execution()

    def load_config(self, config_node, merge_with_existing):
        """
        config_node should be an instance of sheriff_config.ConfigNode
        """
        if self._is_observer:
            raise ValueError("Can't load config in Observer mode")

        # always replace scripts.
        for script in self.scripts[:]:
            self.remove_script(script)

        current_command_strs = set()
        if merge_with_existing:
            # if merging new config with existing commands, then build an index
            # of the existing commands.
            for dep in self.deputies.values():
                for cmd in dep.commands.values():
                    cmdstr = "%s!%s!%s!%s!%s" % (dep.name, cmd.exec_str, cmd.command_id, cmd.group, cmd.auto_respawn)
                    current_command_strs.add(cmdstr)
        else:
            # remove all current commands if we're not merging.
            for dep in self.deputies.values():
                for cmd in dep.commands.values():
                    self.schedule_command_for_removal(cmd)

        commands_to_add = []

        def add_group_commands(group_node, name_prefix):
            for cmd_node in group_node.commands:
                auto_respawn = cmd_node.attributes.get("auto_respawn", "").lower() in [ "true", "yes" ]
                assert group_node.name == cmd_node.attributes["group"]

                add_command = True

                # if merging is enabled, then only add this command if we don't
                # have an entry for it already.
                if merge_with_existing:
                    cmdstr = "%s!%s!%s!%s!%s" % (cmd_node.attributes["host"], cmd_node.attributes["exec"],
                            cmd_node.attributes["nickname"], name_prefix + group_node.name, str(auto_respawn))
                    if cmdstr in current_command_strs:
                        add_command = False

                if add_command:
                    spec = SheriffCommandSpec()
                    spec.deputy_name = cmd_node.attributes["host"]
                    spec.exec_str = cmd_node.attributes["exec"]
                    spec.command_id = cmd_node.attributes["nickname"]
                    spec.group_name = name_prefix + group_node.name
                    spec.auto_respawn = auto_respawn
                    spec.stop_signal = cmd_node.attributes["stop_signal"]
                    spec.stop_time_allowed = cmd_node.attributes["stop_time_allowed"]
                    if spec.stop_signal == 0:
                        spec.stop_signal = DEFAULT_STOP_SIGNAL
                    if spec.stop_time_allowed == 0:
                        spec.stop_time_allowed = DEFAULT_STOP_TIME_ALLOWED

                    commands_to_add.append(spec)

            for subgroup in group_node.subgroups.values():
                if group_node.name:
                    add_group_commands(subgroup, name_prefix + group_node.name + "/")
                else:
                    add_group_commands(subgroup, "")

        add_group_commands(config_node.root_group, "")

        for spec in commands_to_add:
            self.add_command(spec)
#            _dbg("[%s] %s (%s) -> %s" % (newcmd.group, newcmd.exec_str, newcmd.nickname, cmd.attributes["host"]))

        for script_node in config_node.scripts.values():
            self.add_script(SheriffScript.from_script_node(script_node))

    def save_config(self, file_obj):
        """Write the current sheriff configuration to the specified file object.

        The current sheriff configuration consists of all commands managed by all
        deputies along with their settings, and all scripts as well.  This
        information is written out to the specified file object, which can then
        be loaded into the sheriff again at a later point in time.

        @param file_obj a file object for saving the current sheriff configuration
        """
        config_node = sheriff_config.ConfigNode()
        for deputy in self.deputies.values():
            for cmd in deputy.commands.values():
                cmd_node = sheriff_config.CommandNode()
                cmd_node.attributes["exec"] = cmd.exec_str
                cmd_node.attributes["nickname"] = cmd.command_id
                cmd_node.attributes["host"] = deputy.name
                if cmd.auto_respawn:
                    cmd_node.attributes["auto_respawn"] = "true"

                group = config_node.get_group(cmd.group, True)
                group.add_command(cmd_node)
        for script in self.scripts:
            config_node.add_script(script.toScriptNode())
        file_obj.write(str(config_node))

def main():
    def usage():
        print "usage: sheriff.py [config_file [script_name]]"

    args = sys.argv[:]
    if args:
        usage()

    cfg = None
    script_name = None

    if len(args) > 0:
        cfg_fname = args[0]
        cfg = sheriff_config.config_from_filename(cfg_fname)
    if len(args) > 1:
        script_name = args[1]

    comms = lcm.LCM()
    sheriff = Sheriff(comms)
    if cfg is not None:
        sheriff.load_config(cfg, False)

    if script_name is not None:
        script = sheriff.get_script(script_name)
        if not script:
            print "ERROR! Uknown script %s" % script_name
            sys.exit(1)

        errors = sheriff.execute_script(script)
        if errors:
            print "ERROR!  Unable to execute script:\n%s" % "\n  ".join(errors)
            sys.exit(1)

    sheriff.connect("deputy-info-received",
            lambda s, dep: sys.stdout.write("deputy info received from %s\n" %
                dep.name))
    mainloop = gobject.MainLoop()
    gobject.io_add_watch(comms, gobject.IO_IN, lambda *s: comms.handle() or True)
    gobject.timeout_add(1000, lambda *s: sheriff.send_orders() or True)
    mainloop.run()

if __name__ == "__main__":
    main()

"""
@}
"""
