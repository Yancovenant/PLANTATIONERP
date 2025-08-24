# Part of Inphms. See LICENSE file for full copyright and licensing details.
import logging
import os
import sys
from pathlib import Path

import inphms
from inphms.modules import initialize_sys_path, get_modules, get_module_path

commands = {}
class Command: #ichecked
    name = None
    def __init_subclass__(cls):
        cls.name = cls.name or cls.__name__.lower()
        commands[cls.name] = cls

INPHMS_HELP = """\
Inphms CLI, use '{inphms_bin}' --help' for regular server options.

Available commands:
    {command_list}

Use '{inphms_bin} <command> --help' for individual command help."""

class Help(Command): #ichecked
    """ Display list of available commands """
    def run(self, args):
        padding = max([len(cmd) for cmd in commands]) + 2
        command_list = "\n    ".join([
            "    {}{}".format(name.ljust(padding), (command.__doc__ or "").strip())
            for name, command in sorted(commands.items())
        ])
        print(INPHMS_HELP.format(
            inphms_bin=Path(sys.argv[0]).name,
            command_list=command_list
        ))

def main():
    args = sys.argv[1:]

    if len(args) > 1 and args[0].startswith('--addons-path=') and not args[1].startswith("-"):
        # ? if arg is --addons-path=path, parse the config
        inphms.tools.config._parse_config(args[0])
        args = args[1:]
    
    # ? if no args, default to `server`
    command = "server"

    if len(args) and not args[0].startswith("-"):
        # ? if args is not starts with -, then it is a command
        # ? command list is all file inside inphms/cli/commands
        logging.disable(logging.CRITICAL)
        initialize_sys_path()
        for module in get_modules():
            if (Path(get_module_path(module)) / 'cli').is_dir():
                __import__('inphms.addons.' + module)
        logging.disable(logging.NOTSET)
        command = args[0]
        args = args[1:]

    if command in commands:
        i = commands[command]()
        i.run(args)
    else:
        sys.exit('Unknown command %r' % (command,))