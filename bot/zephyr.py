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
import os
import subprocess
import sys
import time
import datetime
import collections
from argparse import Namespace
from git import Repo

import serial
import autoptsclient_common as autoptsclient
import ptsprojects.zephyr as autoprojects
from ptsprojects.testcase_db import DATABASE_FILE
from ptsprojects.zephyr.iutctl import get_iut, log
from pathlib import Path
import bot.common


def check_call(cmd, env=None, cwd=None, shell=True):
    """Run command with arguments.  Wait for command to complete.
    :param cmd: command to run
    :param env: environment variables for the new process
    :param cwd: sets current directory before execution
    :param shell: if true, the command will be executed through the shell
    :return: returncode
    """
    executable = '/bin/bash'
    cmd = subprocess.list2cmdline(cmd)

    if sys.platform == 'win32':
        executable = None
        shell = False

    return subprocess.check_call(cmd, env=env, cwd=cwd, shell=shell, executable=executable)


def build_and_flash(zephyr_wd, board, jlink_srn, conf_file=None):
    """Build and flash Zephyr binary
    :param zephyr_wd: Zephyr source path
    :param board: IUT
    :param jlink_srn
    :param conf_file: configuration file to be used
    """
    logging.debug("%s: %s %s %s", build_and_flash.__name__, zephyr_wd,
                                         board, conf_file)
    tester_dir = os.path.join(zephyr_wd, "tests", "bluetooth", "tester")

    check_call('rm -rf build/'.split(), cwd=tester_dir)

    # If nrf53 power-cycled, use the hack to fix hw_flow_control --BEGIN
    if board == 'nrf53' and conf_file == 'prj.conf':
        repo = Repo(zephyr_wd)
        nrf5340_overlay_conf = os.path.join(zephyr_wd, 'tests', 'bluetooth',
                                            'tester', 'nrf5340dk_nrf5340_cpuapp.overlay')
        if repo.is_dirty(path=nrf5340_overlay_conf):
            repo.index.checkout(paths=nrf5340_overlay_conf, force=True)

        lines = []
        with open(nrf5340_overlay_conf, 'r') as file:
            lines = file.readlines()
            lines = lines[:-2]
            lines.append('};')
        with open(nrf5340_overlay_conf, 'w') as file:
            file.writelines(lines)

        cmd = ['west', 'build', '-p', 'always', '-b', board]
        check_call(cmd, cwd=tester_dir)
        check_call(['west', 'flash', '--recover', '--erase', '--skip-rebuild',
                    '--snr', jlink_srn], cwd=tester_dir)

        repo.index.checkout(paths=nrf5340_overlay_conf, force=True)
    # --END

    cmd = ['west',  'build', '-p', 'always', '-b', board]
    if conf_file and conf_file != 'default' and conf_file != 'prj.conf':
        cmd.extend(('--', '-DOVERLAY_CONFIG={}'.format(conf_file)))

    if sys.platform == 'win32':
        cmd = subprocess.list2cmdline(cmd)
        cmd = ['bash.exe', '-c', '-i', cmd]  # bash.exe == wsl

    check_call(cmd, cwd=tester_dir)
    check_call(['west', 'flash', '--recover', '--erase', '--skip-rebuild',
                '--snr', jlink_srn], cwd=tester_dir)


def flush_serial(tty):
    """Clear the serial port buffer
    :param tty: file path of the terminal
    :return: None
    """
    if not tty:
        return

    if sys.platform == 'win32':
        com = "COM" + str(int(tty["/dev/ttyS".__len__():]) + 1)
        ser = serial.Serial(com, 115200, timeout=5)
        ser.flushInput()
        ser.flushOutput()
    else:
        check_call(['while', 'read', '-t', '0', 'var', '<', tty, ';', 'do',
                    'continue;', 'done'])


def apply_overlay(zephyr_wd, base_conf, cfg_name, overlay):
    """Duplicates default_conf configuration file and applies overlay changes
    to it.
    :param zephyr_wd: Zephyr source path
    :param base_conf: base configuration file
    :param cfg_name: new configuration file name
    :param overlay: defines changes to be applied
    :return: None
    """
    tester_app_dir = os.path.join(zephyr_wd, "tests", "bluetooth", "tester")
    cwd = os.getcwd()

    os.chdir(tester_app_dir)

    with open(cfg_name, 'w') as config:
        for k, v in list(overlay.items()):
            config.write("{}={}\n".format(k, v))

    os.chdir(cwd)


autopts2board = {
    None: None,
    'nrf52': 'nrf52840dk_nrf52840',
    'nrf53': 'nrf5340dk_nrf5340_cpuapp',
    'reel_board' : 'reel_board'
}


def get_tty_path(name):
    """Returns tty path (eg. /dev/ttyUSB0) of serial device with specified name
    :param name: device name
    :return: tty path if device found, otherwise None
    """
    serial_devices = {}
    ls = subprocess.Popen(["ls", "-l", "/dev/serial/by-id"],
                          stdout=subprocess.PIPE)

    awk = subprocess.Popen("awk '{if (NF > 5) print $(NF-2), $NF}'",
                           stdin=ls.stdout,
                           stdout=subprocess.PIPE,
                           shell=True)

    end_of_pipe = awk.stdout
    for line in end_of_pipe:
        device, filepath = line.decode().rstrip().split(" ")
        serial_devices[device] = filepath

    for device, filepath in list(serial_devices.items()):
        if name in device:
            tty = os.path.basename(filepath)
            return "/dev/{}".format(tty)

    return None


def zephyr_hash_url(commit):
    """ Create URL for commit in Zephyr
    :param commit: Commit ID to append
    :return: URL of commit
    """
    return "{}/commit/{}".format(autoprojects.ZEPHYR_PROJECT_URL,
                                 commit)
class PtsInitArgs:
    """
    Translates arguments provided in 'config.py' file to be used by
    'autoptsclient.init_pts' function
    """

    def __init__(self, args):
        self.workspace = args["workspace"]
        self.bd_addr = args["bd_addr"]
        self.enable_max_logs = args.get('enable_max_logs', False)
        self.retry = args.get('retry', 0)
        self.stress_test = args.get('stress_test', False)
        self.test_cases = []
        self.excluded = []
        self.srv_port = args.get('srv_port', [65000])
        self.cli_port = args.get('cli_port', [65001])
        self.ip_addr = args.get('server_ip', ['127.0.0.1'] * len(self.srv_port))
        self.local_addr = args.get('local_ip', ['127.0.0.1'] * len(self.cli_port))
        self.ykush = args.get('ykush', None)
        self.recovery = args.get('recovery', False)
        self.superguard = 60 * float(args.get('superguard', 0))


def run_tests(args, iut_config, tty, jlink_srn):
    """Run test cases
    :param args: AutoPTS arguments
    :param iut_config: IUT configuration
    :param tty path
    :return: tuple of (status, results) dictionaries
    """
    results = {}
    status = {}
    descriptions = {}
    total_regressions = []
    _args = {}

    config_default = "prj.conf"
    _args[config_default] = PtsInitArgs(args)

    for config, value in list(iut_config.items()):
        if 'test_cases' not in value:
            # Rename default config
            _args[config] = _args.pop(config_default)
            config_default = config
            continue

        if config != config_default:
            _args[config] = PtsInitArgs(args)

        _args[config].test_cases = value.get('test_cases', [])

        if 'overlay' in value:
            _args[config_default].excluded += _args[config].test_cases

    while True:
        try:
            ptses = autoptsclient.init_pts(_args[config_default],
                                           "zephyr_" + str(args["board"]))

            btp.init(get_iut)

            # Main instance of PTS
            pts = ptses[0]

            # Read PTS Version and keep it for later use
            args['pts_ver'] = "%s" % pts.get_version()
        except Exception as exc:
            logging.exception(exc)
            if _args[config_default].recovery:
                ptses = exc.args[1]
                for pts in ptses:
                    autoptsclient.recover_autoptsserver(pts)
                time.sleep(20)
                continue
            raise exc
        break

    stack.init_stack()
    stack_inst = stack.get_stack()
    stack_inst.synch_init([pts.callback_thread for pts in ptses])

    for config, value in list(iut_config.items()):
        if 'overlay' in value:
            apply_overlay(args["project_path"], config_default, config,
                          value['overlay'])

        build_and_flash(args["project_path"],
                        autopts2board[args["board"]],
                        jlink_srn,
                        config)
        logging.debug("TTY path: %s", tty)

        flush_serial(tty)
        time.sleep(10)

        autoprojects.iutctl.init(Namespace(kernel_image=args["kernel_image"],
                                           tty_file=tty, board=args["board"],
                                           hci=None, rtt2pty=None))


def make_readme_md(start_time, end_time, commit_sha, pts_ver):
    """Creates README.md for Github logging repo
    """
    readme_file = 'tmp/README.md'

    Path(os.path.dirname(readme_file)).mkdir(parents=True, exist_ok=True)

    with open(readme_file, 'w') as f:
        readme_body = '''# AutoPTS report

    Start time: {}

    End time: {}

    PTS version: {}

    HEAD commit: {} [{}]
    '''.format(start_time, end_time, pts_ver, commit_sha['commit'], commit_sha['desc'])

        f.write(readme_body)

    return readme_file


def compose_mail(args, mail_cfg, mail_ctx):
    """ Create a email body
    """

    iso_cal = datetime.date.today().isocalendar()
    ww_dd_str = "WW%s.%s" % (iso_cal[1], iso_cal[2])

    body = '''
    <p>This is automated email and do not reply.</p>
    <h1>Bluetooth test session - {} </h1>
    <h2>1. IUT Setup</h2>
    <p><b> Type:</b> Zephyr <br>
    <b> Board:</b> {} <br>
    <b> Source:</b> {} </p>
    <h2>2. PTS Setup</h2>
    <p><b> OS:</b> Windows 10 <br>
    <b> Platform:</b> {} <br>
    <b> Version:</b> {} </p>
    <h2>3. Test Results</h2>
    <p><b>Execution Time</b>: {}</p>
    {}
    {}
    <h3>Logs</h3>
    {}
    <p>Sincerely,</p>
    <p> {}</p>
    '''.format(ww_dd_str, args["board"], mail_ctx["zephyr_hash"], args['platform'],
               args['pts_ver'], mail_ctx["elapsed_time"], mail_ctx["summary"],
               mail_ctx["regression"], mail_ctx["log_url"], mail_cfg['name'])

    if 'subject' in mail_cfg:
        subject = mail_cfg['subject']
    else:
        subject = "AutoPTS test session results"

    subject = "%s - %s" % (subject, ww_dd_str)

    return subject, body


class ZephyrBotConfigArgs(bot.common.BotConfigArgs):
    def __init__(self, args):
        super().__init__(args)
        self.board_name = args['board']
        self.tty_file = args['tty_file']


class ZephyrBotCliParser(bot.common.BotCliParser):
    def __init__(self, add_help=True):
        super().__init__(description="PTS automation client",
                         board_names=autoprojects.iutctl.Board.names,
                         add_help=add_help)


class ZephyrBotClient(bot.common.BotClient):
    def __init__(self):
        super().__init__(get_iut, 'zephyr', autoprojects.iutctl.Board.names)
        self.arg_parser = ZephyrBotCliParser()
        self.parse_config = ZephyrBotConfigArgs
        self.config_default = "prj.conf"

    def apply_config(self, args, config, value):
        if 'overlay' in value:
            apply_overlay(args.project_path, self.config_default, config,
                          value['overlay'])

        log("TTY path: %s" % args.tty_file)

        if not args.no_build:
            build_and_flash(args.project_path, autopts2board[args.board_name],
                            args.tty_file, config)

            flush_serial(args.tty_file)
            time.sleep(10)


class ZephyrClient(autoptsclient.Client):
    def __init__(self):
        super().__init__(get_iut, 'zephyr', autoprojects.iutctl.Board.names)


SimpleClient = ZephyrClient
BotCliParser = ZephyrBotCliParser


def main(cfg):

    bot.common.pre_cleanup()

    start_time = time.time()
    start_time_stamp = datetime.datetime.now().strftime("%Y_%m_%d_%H_%M_%S")

    args = cfg['auto_pts']

    if 'database_file' not in args:
        args['database_file'] = DATABASE_FILE

    args['kernel_image'] = os.path.join(args['project_path'], 'tests',
                                        'bluetooth', 'tester', 'outdir',
                                        'zephyr', 'zephyr.elf')

    if 'git' in cfg:
        zephyr_hash = bot.common.update_repos(args['project_path'],
                                              cfg['git'])['zephyr']
    else:
        zephyr_hash = {'desc': '', 'commit': ''}

    if 'ykush' in args:
        autoptsclient.board_power(args['ykush'], True)
        time.sleep(1)

    tty, jlink_srn = bot.common.get_free_device(args['board'])

    try:
        summary, results, descriptions, regressions = \
            run_tests(args, cfg.get('iut_config', {}), tty, jlink_srn)
    except Exception as e:
        bot.common.release_device(jlink_srn)
        raise e

    bot.common.release_device(jlink_srn)
    results = collections.OrderedDict(sorted(results.items()))

    report_file = bot.common.make_report_xlsx(results, summary, regressions,
                                              descriptions)
    report_txt = bot.common.make_report_txt(results, zephyr_hash["desc"])

    end_time = time.time()
    end_time_stamp = datetime.datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    url = None
    github_link = None
    report_folder = None

    if 'githubdrive' in cfg or 'gdrive' in cfg:
        args_ns = ZephyrBotConfigArgs(args)
        pts_logs = bot.common.pull_server_logs(args_ns)
        iut_logs = 'logs/'
        readme_file = make_readme_md(start_time_stamp, end_time_stamp,
                                     zephyr_hash, args['pts_ver'])
        report_folder = bot.common.make_report_folder(iut_logs, pts_logs, report_file,
                                                      report_txt, readme_file,
                                                      args['database_file'],
                                                      '_iut_zephyr_' + start_time_stamp)

    if 'githubdrive' in cfg:
        print("Uploading to Github ...")
        commit_msg_pattern = '{branch}_{timestamp}_{commit_sha}'
        branch = 'no_branch'
        commit_sha = 'no_sha'

        if 'commit_msg' in cfg['githubdrive'] and \
                cfg['githubdrive']['commit_msg'] is not None:
            commit_msg_pattern = cfg['githubdrive']['commit_msg']

        if 'git' in cfg:
            commit_sha = zephyr_hash['commit']
            branch = cfg['git']['zephyr']['branch']

        commit_msg = commit_msg_pattern.format(
            timestamp=start_time_stamp, branch=branch, commit_sha=commit_sha)
        github_link, report_folder = bot.common.github_push_report(
            report_folder, cfg['githubdrive'], commit_msg)

    if 'gdrive' in cfg:
        print("Uploading to GDrive ...")
        bot.common.archive_testcases(report_folder, depth=2)
        drive = bot.common.Drive(cfg['gdrive'])
        url = drive.new_workdir(args['board'])
        drive.upload_folder(report_folder)

    if 'mail' in cfg:
        print("Sending email ...")

        # keep mail related context to simplify the code
        mail_ctx = {"summary": bot.common.status_dict2summary_html(summary),
                    "regression": bot.common.regressions2html(regressions,
                                                              descriptions)}

        # Summary

        # Regression and test case description

        # Zephyr commit id link in HTML format

        # Commit id may have "-dirty" if the source is dirty.
        commit_id = zephyr_hash["commit"]
        if commit_id.endswith('-dirty'):
            commit_id = commit_id[:-6]
        mail_ctx["zephyr_hash"] = bot.common.url2html(zephyr_hash_url(commit_id),
                                                      zephyr_hash["desc"])

        # Log in Google drive in HTML format
        if 'gdrive' in cfg and url:
            mail_ctx["log_url"] = bot.common.url2html(url, "Results on Google Drive")

        if 'githubdrive' in cfg and github_link:
            if 'log_url' in mail_ctx:
                mail_ctx["log_url"] += '<br>'
            else:
                mail_ctx["log_url"] = ''
            mail_ctx['log_url'] += bot.common.url2html(github_link, 'Results on Github')

        if 'log_url' not in mail_ctx:
            mail_ctx['log_url'] = 'Not Available'

        # Elapsed Time
        mail_ctx["elapsed_time"] = str(datetime.timedelta(
                                       seconds=(end_time - start_time)))

        subject, body = compose_mail(args, cfg['mail'], mail_ctx)

        bot.common.send_mail(cfg['mail'], subject, body,
                             [report_file, report_txt])

        print("Done")

    bot.common.cleanup()

    print("\nBye!")
    sys.stdout.flush()
    return 0
