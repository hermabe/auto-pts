#!/usr/bin/env python

#
# auto-pts - The Bluetooth PTS Automation Framework
#
# Copyright (c) 2018, Intel Corporation.
#
# This program is free software; you can redistribute it and/or modify it
# under the terms and conditions of the GNU General Public License,
# version 2, as published by the Free Software Foundation.
#
# This program is distributed in the hope it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for
# more details.
#
import logging
import sys
import time
import _locale

import schedule

from bot.config import BotProjects
from bot.zephyr import main as zephyr
from bot.mynewt import main as mynewt
from winutils import have_admin_rights


# TODO Find more sophisticated way
weekdays2schedule = {
    'monday': schedule.every().monday,
    'tuesday': schedule.every().tuesday,
    'wednesday': schedule.every().wednesday,
    'thursday': schedule.every().thursday,
    'friday': schedule.every().friday,
    'saturday': schedule.every().saturday,
    'sunday': schedule.every().sunday,
}

project2main = {
    'zephyr': zephyr,
    'mynewt': mynewt,
}


def main():
    # Workaround for logging error: "UnicodeEncodeError: 'charmap' codec can't
    # encode character '\xe6' in position 138: character maps to <undefined>",
    # which occurs under Windows with default encoding other than cp1252
    # each time log() is called.
    _locale._getdefaultlocale = (lambda *args: ['en_US', 'utf8'])

    for project in BotProjects:
        # TODO Solve the issue of overlapping jobs
        if 'scheduler' in project:
            for day, time_ in list(project['scheduler'].items()):
                weekdays2schedule[day].at(time_).do(
                    project2main[project['name']], project)

            while True:
                schedule.run_pending()
                time.sleep(60)
        else:
            project2main[project['name']](project)


if __name__ == "__main__":
    if have_admin_rights():  # root privileges are not needed
        print("Please do not run this program as root.")
        sys.exit(1)

    try:
        main()
        sys.exit(0)
    except KeyboardInterrupt:  # Ctrl-C
        sys.exit(14)
    except SystemExit:
        raise
    except BaseException as e:
        logging.exception(e)
        import traceback

        traceback.print_exc()
        sys.exit(16)
