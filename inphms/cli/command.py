# Part of Inphms. See LICENSE file for full copyright and licensing details.
import logging
import os
import sys
from pathlib import Path

commands = {}
class Command:
    name = None
    def __init_subclass__(cls):
        cls.name = cls.name or cls.__name__.lower()
        commands[cls.name] = cls

INPHMS_HELP = """\
Inphms CLI, use 'inphms_bin' --help' for regular server options.

Available commands:
    {command_list}

Use '{inphms_bin} <command> --help' for individual command help."""

class Help(Command):
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
        inphms.tools.config._parse_config(args[0])
        args = args[1:]
    
    command = "server"

    if command in commands:
        i = commands[command]()
        i.run(args)
    else:
        sys.exit('Unknown command %r' % (command,))