# -*- coding: utf-8 -*-
# @author: Tomas Vitvar, https://vitvar.com, tomas.vitvar@oracle.com

import click
import signal
import traceback
import sys

from .log import soa
from .click_ext import CoreCommandGroup
from .. import config

from wls_analytics import __version__


@click.group(cls=CoreCommandGroup)
@click.option("--no-ansi", "no_ansi", is_flag=True, default=not config.ANSI_COLORS, help="No ANSI colors.")
@click.option(
    "--debug",
    "debug",
    is_flag=True,
    default=config.DEBUG,
    help="Print debug information.",
)
@click.option("--traceback", "traceback", is_flag=True, default=config.TRACEBACK, help="Print traceback for errors.")
@click.version_option(version=__version__)
def wls_analytics():
    pass


wls_analytics.add_command(soa)
