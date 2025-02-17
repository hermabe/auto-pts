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
import re
import subprocess
import sys
import mimetypes
import shutil
import zipfile
import smtplib
import datetime
from os.path import dirname, abspath
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

from xmlrpc.client import ServerProxy
import git
import yaml
import xlsxwriter

from googleapiclient import discovery, errors
from googleapiclient.http import MediaFileUpload
from httplib2 import Http
from oauth2client import file, client, tools

SCOPES = 'https://www.googleapis.com/auth/drive'
CLIENT_SECRET_FILE = 'client_secret.json'
REPORT_XLSX = "report.xlsx"
REPORT_TXT = "report.txt"
COMMASPACE = ', '

devices_in_use = []
PROJECT_DIR = dirname(dirname(abspath(__file__)))

# ****************************************************************************
# Mail
# ****************************************************************************


def status_dict2summary_html(status_dict):
    """Creates HTML formatted summary from status dictionary
    :param status_dict: status dictionary, where key is status and value is
    status count
    :return: HTML formatted summary
    """
    summary = """<h3>Summary</h3>
                 <table>"""
    total_count = 0

    summary += """<tr>
                  <td style=\"width: 150px;\"><b>Status</b></td>
                  <td style=\"text-align: center;\"><b>Count</b></td>
                  </tr>"""

    for status in sorted(status_dict.keys()):
        count = status_dict[status]
        summary += """<tr>
                      <td style=\"width: 150px;\">{}</td>
                      <td style=\"text-align: center;\">{}</td>
                      </tr>""".format(status, count)
        total_count += count

    summary += """<tr>
                  <td style=\"width: 150px;\"><i>Total</i></td>
                  <td style=\"text-align: center;\"><i>{}</i></td>
                  </tr>""".format(total_count)
    summary += "</table>"

    if "PASS" in status_dict:
        pass_rate = \
            '{0:.2f}%'.format((status_dict["PASS"] / float(total_count) * 100))
    else:
        pass_rate = '{0:.2f}%'.format(0)
    summary += "<p><b>PassRate = {}</b></p>".format(pass_rate)

    return summary


def url2html(url, msg):
    """Creates HTML formatted URL with results
    :param url: URL
    :param msg: URL description
    :return: HTML formatted URL
    """
    return "<a href={}>{}</a>".format(url, msg)


def regressions2html(regressions, descriptions):
    """Creates HTML formatted message with regressions
    :param regressions_list: list of regressions found
    :return: HTML formatted message
    """
    msg = "<h3>Regressions</h3>"

    regressions_list = []
    for name in regressions:
        regressions_list.append(
            name + " - " + descriptions.get(name, "no description"))

    if regressions_list:
        for name in regressions_list:
            msg += "<p>{}</p>".format(name)
    else:
        msg += "<p>No regressions found</p>"

    return msg


def send_mail(cfg, subject, body, attachments=None):
    """
    :param cfg: Mailbox configuration
    :param subject: Mail subject
    :param body: Mail boyd
    :return: None
    """

    msg = MIMEMultipart()
    msg['From'] = cfg['sender']
    msg['To'] = COMMASPACE.join(cfg['recipients'])
    msg['Subject'] = subject

    msg.attach(MIMEText(body, 'html'))

    # Attach the files if there is any
    if attachments:
        for filename in attachments:
            file_type = mimetypes.guess_type(filename)
            if file_type[0] is None:
                ext = os.path.splitext(filename)[1]
                print('MIME Error: File extension %s is unknown. '
                      'Try to associate it with app.' % ext)
                continue
            mimetype = file_type[0].split('/', 1)
            attachment = MIMEBase(mimetype[0], mimetype[1])
            attachment.set_payload(open(filename, 'rb').read())
            encoders.encode_base64(attachment)
            attachment.add_header('Content-Disposition', 'attachment',
                                  filename=os.path.basename(filename))
            msg.attach(attachment)

    server = smtplib.SMTP(cfg['smtp_host'], cfg['smtp_port'])
    if 'start_tls' in cfg and cfg['start_tls']:
        server.starttls()
    if 'passwd' in cfg:
        server.login(cfg['sender'], cfg['passwd'])
    server.sendmail(cfg['sender'], cfg['recipients'], msg.as_string())
    server.quit()


# ****************************************************************************
# Google Drive
# ****************************************************************************
class GDrive:
    def __init__(self, cfg):
        self.basedir_id = cfg['root_directory_id']
        self.cwd_id = self.basedir_id
        credentials = cfg['credentials_file']

        store = file.Storage(credentials)
        creds = store.get()
        if not creds or creds.invalid:
            path_abs = os.path.abspath(credentials)
            path = os.path.dirname(path_abs)

            flow = client.flow_from_clientsecrets(
                os.path.join(path, CLIENT_SECRET_FILE), SCOPES)
            creds = tools.run_flow(flow, store)
        self.service = discovery.build('drive', 'v3',
                                       http=creds.authorize(Http()))

    def pwd(self):
        return self.cwd_id

    def mkdir(self, name):
        file_metadata = {
            'name': name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [self.pwd()]
        }

        try:
            f = self.service.files().create(
                body=file_metadata,
                fields='id, name, webViewLink').execute()
        except errors.HttpError:
            sys.exit(1)

        return f

    def ls(self):
        results = {}

        page_token = None
        while True:
            try:
                response = self.service.files().list(
                    q="'{}' in parents".format(self.pwd()),
                    spaces='drive',
                    fields='nextPageToken, files(id, name)',
                    pageToken=page_token).execute()
            except errors.HttpError:
                sys.exit(1)

            for f in response.get('files', []):
                results[f.get('name')] = f
            page_token = response.get('nextPageToken', None)
            if page_token is None:
                break

        return results

    def cp(self, name):
        if not os.path.exists(name):
            print("File not found")
            sys.exit(1)

        basename = os.path.basename(name)
        mime_type, encoding = mimetypes.guess_type(basename)

        file_metadata = {
            'name': basename,
            'parents': [self.pwd()]
        }

        media = MediaFileUpload(
            name,
            mimetype=mime_type)

        try:
            f = self.service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, name').execute()
        except errors.HttpError as err:
            print(err)
            sys.exit(1)

        return f

    def cd(self, dir_=None):
        """
            :param dir_: file object or id of the folder
        """
        if not dir_:
            self.cwd_id = self.basedir_id
        elif isinstance(dir_, str):
            self.cwd_id = dir_
        else:
            self.cwd_id = dir_.get('id')


class Drive(GDrive):
    def __init__(self, cfg):
        GDrive.__init__(self, cfg)
        self.url = None

    def new_workdir(self, iut):
        files = self.ls()
        if iut in list(files.keys()):
            dir_ = files[iut]
        else:
            dir_ = self.mkdir(iut)
        self.cd(dir_)
        dir_ = self.mkdir(datetime.datetime.now().strftime("%Y_%m_%d_%H_%M"))
        self.cd(dir_)
        return "{}".format(dir_.get('webViewLink'))

    def upload(self, f):
        print("Uploading {} ...".format(f))
        self.cp(f)
        print("Done")

    def upload_folder(self, folder, excluded=None):
        def recursive(directory):
            with os.scandir(directory) as it:
                for f in it:
                    if excluded and (f.name in excluded or
                                     os.path.splitext(f.name)[1] in excluded):
                        continue

                    if f.is_dir():
                        parent = self.pwd()
                        dir_ = self.mkdir(f.name)
                        self.cd(dir_)
                        recursive(os.path.join(directory, f.name))
                        self.cd(parent)
                    else:
                        filepath = os.path.relpath(os.path.join(directory, f.name))
                        self.upload(filepath)

        recursive(folder)


# ****************************************************************************
# .xlsx spreadsheet file
# ****************************************************************************
# FIXME don't use statuses from status_dict, count it from results dict instead
def make_report_xlsx(results_dict, status_dict, regressions_list,
                     descriptions):
    """Creates excel file containing test cases results and summary pie chart
    :param results_dict: dictionary with test cases results
    :param status_dict: status dictionary, where key is status and value is
    status count
    :param regressions_list: list of regressions found
    :return:
    """

    errata = {}

    try:
        with open('errata.yaml', 'r') as stream:
            errata = yaml.safe_load(stream)
    except Exception as exc:
        print(exc)

    if errata is None:
        errata = {}

    header = "AutoPTS Report: " \
             "{}".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    workbook = xlsxwriter.Workbook(REPORT_XLSX)
    worksheet = workbook.add_worksheet()
    chart = workbook.add_chart({'type': 'pie',
                                'subtype': 'percent_stacked'})

    # Add a bold format to use to highlight cells.
    bold = workbook.add_format({'bold': True})

    # Write data headers.
    worksheet.write('A1', header)
    worksheet.write_row('A3', ['Test Case', 'Result'])

    row = 3
    col = 0

    for k, v in list(results_dict.items()):
        worksheet.write(row, col, k)
        if k in errata:
            v += ' - ERRATA ' + errata[k]
        worksheet.write(row, col + 1, v)
        if k in list(descriptions.keys()):
            worksheet.write(row, col + 2, descriptions[k])
        if k in regressions_list:
            worksheet.write(row, col + 3, "REGRESSION")
        row += 1

    summary_row = 2
    summary_col = 5

    worksheet.write(summary_row, summary_col, 'Summary')
    end_row = summary_row
    for status in sorted(status_dict.keys()):
        count = status_dict[status]
        end_row += 1
        worksheet.write_row(end_row, summary_col, [status, count])

    # Total TCS
    row = end_row + 2
    col = summary_col
    total_count = len(results_dict)
    worksheet.write(row, col, "Total")
    worksheet.write(row, col + 1, "{}".format(total_count))
    worksheet.write(row + 1, col, "PassRate", bold)
    if "PASS" in status_dict:
        pass_rate = \
            '{0:.2f}%'.format((status_dict["PASS"] / float(total_count) * 100))
    else:
        pass_rate = '{0:.2f}%'.format(0)
    worksheet.write(row + 1, col + 1, pass_rate, bold)

    chart.set_title({'name': 'AutoPTS test results'})
    chart.add_series({
        'categories': ['Sheet1', summary_row + 1, summary_col,
                       end_row, summary_col],
        'values': ['Sheet1', summary_row + 1, summary_col + 1,
                   end_row, summary_col + 1],
    })

    worksheet.insert_chart('H2', chart)
    workbook.close()

    return os.path.join(os.getcwd(), REPORT_XLSX)


# ****************************************************************************
# .txt result file
# ****************************************************************************
def make_report_txt(results_dict, zephyr_hash):
    """Creates txt file containing test cases results
    :param results_dict: dictionary with test cases results
    :return: txt file path
    """

    filename = os.path.join(os.getcwd(), REPORT_TXT)
    f = open(filename, "w")

    errata = {}

    try:
        with open('errata.yaml', 'r') as stream:
            errata = yaml.safe_load(stream)
    except Exception as exc:
        print(exc)

    if errata is None:
        errata = {}

    f.write("%s\n" % zephyr_hash)
    for tc, result in list(results_dict.items()):
        if tc in errata:
            result += ' - ERRATA ' + errata[tc]

        # The frist id in the test case is test group
        tg = tc.split('/')[0]
        f.write("%s%s%s\n" % (tg.ljust(8, ' '), tc.ljust(32, ' '), result))

    f.close()

    return filename


# ****************************************************************************
# Miscellaneous
# ****************************************************************************
def archive_recursive(dir_path):
    """Archive directory recursively
    :return: newly created zip file path
    """
    zip_file_path = os.path.join(os.path.dirname(dir_path),
                                 os.path.basename(dir_path) + '.zip')
    with zipfile.ZipFile(zip_file_path, 'w', allowZip64=True) as zf:
        for root, dirs, files in os.walk(dir_path):
            for file_or_dir in files + dirs:
                zf.write(
                    os.path.join(root, file_or_dir),
                    os.path.relpath(os.path.join(root, file_or_dir),
                                    os.path.join(dir_path, os.path.pardir)))

    return zip_file_path


def archive_testcases(dir_path, depth=3):
    def recursive(directory, depth):
        depth -= 1
        with os.scandir(directory) as it:
            for f in it:
                if f.is_dir():
                    if depth > 0:
                        recursive(os.path.join(directory, f.name), depth)
                    else:
                        filepath = os.path.relpath(os.path.join(directory, f.name))
                        archive_recursive(filepath)
                        shutil.rmtree(filepath)

    recursive(dir_path, depth)
    return dir_path


def upload_bpv_logs(gdrive, args):
    """Copy Bluetooth Protocol Viewer logs from auto-pts servers.
    :param gdrive: to upload the logs
    :param server_addr: list of servers addresses
    :param server_port: list of servers ports
    """
    excluded = ['SIGDatabase', 'logfiles', '.pqw6', '.xml', '.txt']
    logs_folder = 'tmp/' + args.workspace

    shutil.rmtree(logs_folder, ignore_errors=True)

    if sys.platform == 'win32':
        workspace_path = get_workspace(args.workspace)
        shutil.copytree(workspace_path, logs_folder)
        archive_testcases(logs_folder, depth=3)
        gdrive.upload_folder(logs_folder, excluded=excluded)
        delete_bpv_logs(workspace_path)
        return

    server_addr = args.ip_addr
    server_port = args.srv_port

    for i in range(len(server_addr)):
        if i != 0 and server_addr[i] in server_addr[0:i]:
            continue

        with ServerProxy("http://{}:{}/".format(server_addr[i], server_port[i]),
                         allow_none=True,) as proxy:
            file_list = proxy.list_workspace_tree(args.workspace)
            if len(file_list) == 0:
                continue

            workspace_root = file_list.pop()
            while len(file_list) > 0:
                file_path = file_list.pop(0)
                try:
                    file_bin = proxy.copy_file(file_path)

                    if not os.path.splitext(file_path)[1] in ['.pts', '.pqw6']:
                        proxy.delete_file(file_path)

                    if file_bin is None:
                        continue

                    file_path = '/'.join([logs_folder,
                                          file_path[len(workspace_root) + 1:]
                                          .replace('\\', '/')])
                    Path(os.path.dirname(file_path)).mkdir(parents=True,
                                                           exist_ok=True)

                    with open(file_path, 'wb') as handle:
                        handle.write(file_bin.data)
                except BaseException as e:
                    logging.exception(e)

    if os.path.exists(logs_folder):
        archive_testcases(logs_folder, depth=3)
        gdrive.upload_folder(logs_folder, excluded=excluded)


def get_workspace(workspace):
    for root, dirs, files in os.walk(os.path.join(PROJECT_DIR, 'workspaces'),
                                     topdown=True):
        for name in dirs:
            if name == workspace:
                return os.path.join(root, name)
    return None


def delete_bpv_logs(workspace_path):
    with os.scandir(workspace_path) as it:
        for f in it:
            if f.is_dir():
                shutil.rmtree(f.path, ignore_errors=True)


def update_sources(repo_path, remote, branch, stash_changes=False, update_repo=True):
    """GIT Update sources
    :param repo: git repository path
    :param remote: git repository remote name
    :param branch: git repository branch name
    :param stash_changes: stash non-committed changes
    :param update_repo: update repo
    :return: Commit SHA at HEAD
    """
    repo = git.Repo(repo_path)

    if update_repo:
        print('Updating ' + repo_path)

        dirty = repo.is_dirty()
        if dirty and (not stash_changes):
            print('Repo is dirty. Not updating')
            return repo.git.describe('--always'), \
                repo.git.show('-s', '--format=%H') + '-dirty'

        if dirty and stash_changes:
            print('Repo is dirty. Stashing changes')
            repo.git.stash('--include-untracked')

        repo.git.fetch(remote)
        repo.git.checkout('{}/{}'.format(remote, branch))

    return repo.git.describe('--always'), \
        repo.git.show('-s', '--format=%H')


def update_repos(project_path, git_config):
    """GIT Update sources
    :param project_path: path to project root
    :param git_config: dictionary with configuration of repositories
    :return: repos_dict with {key=repo name, {commit, desc}}
    """
    project_path = os.path.abspath(project_path)
    repos_dict = {}

    for repo, conf in list(git_config.items()):
        repo_dict = {}
        if not os.path.isabs(conf["path"]):
            repo_path = os.path.join(project_path, conf["path"])
        else:
            repo_path = os.path.abspath(conf["path"])

        project_path.join(repo_path)

        if 'update_repo' in conf:
            update_repo = conf["update_repo"]
        else:
            update_repo = True

        desc, commit = update_sources(repo_path, conf["remote"],
                                      conf["branch"], conf["stash_changes"],
                                      update_repo)
        repo_dict["commit"] = commit
        repo_dict["desc"] = desc
        repos_dict[repo] = repo_dict

    return repos_dict


def get_free_device(board=None):
    tty = None
    jlink = None

    snr_initials_for_debugger = {
        "nrf52": '68',
        "nrf53": '96'
    }

    com_index_for_debugger = {
        "nrf52": '00',
        "nrf53": '04'
    }

    debugger_snrs = subprocess.Popen('nrfjprog -i',
                                 shell=True,
                                 stdout=subprocess.PIPE
                                 ).stdout.read().decode()

    debugger_snrs = debugger_snrs.split()

    for d_snr in debugger_snrs:
        if d_snr[:2] != snr_initials_for_debugger[board]:
            continue

        d_tty = subprocess.Popen('ls -l /dev/serial/by-id' +
                                    '/usb-SEGGER_J-Link_000' + d_snr +
                                    '-if' + com_index_for_debugger[board],
                                    shell=True,
                                    stdout=subprocess.PIPE
                                    ).stdout.read().decode()
        reg = "(?=tty).+$"
        d_tty = re.findall(reg, d_tty)

        if d_snr not in devices_in_use:
            devices_in_use.append(d_snr)
            jlink = d_snr
            tty = '/dev/' + d_tty[0]
            break

    if not tty:
        sys.exit('No free device found!')

    if tty.startswith("COM"):
        tty = "/dev/ttyS" + str(int(tty["COM".__len__():]) - 1)

    return tty, jlink


def release_device(jlink_srn):
    if jlink_srn:
        devices_in_use.remove(jlink_srn)


def pre_cleanup():
    """Perform cleanup before test run
    :return: None
    """
    try:
        shutil.copytree("logs", "oldlogs", dirs_exist_ok=True)
        shutil.rmtree("logs")
    except OSError:
        pass


def cleanup():
    """Perform cleanup
    :return: None
    """
    try:
        pass
    except OSError:
        pass
