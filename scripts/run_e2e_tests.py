# Copyright 2019 The Oppia Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the 'License');
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an 'AS-IS' BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Python execution for running e2e tests."""

from __future__ import absolute_import  # pylint: disable=import-only-modules
from __future__ import unicode_literals  # pylint: disable=import-only-modules

import argparse
import atexit
import contextlib
import os
import re
import signal
import subprocess
import sys
import time

import python_utils
from scripts import build
from scripts import common
from scripts import install_chrome_on_travis
from scripts import install_third_party_libs

WEB_DRIVER_PORT = 4444
GOOGLE_APP_ENGINE_PORT = 9001
OPPIA_SERVER_PORT = 8181
PROTRACTOR_BIN_PATH = os.path.join(
    common.NODE_MODULES_PATH, 'protractor', 'bin', 'protractor')
# Path relative to current working directory where portserver socket
# file will be created.
PORTSERVER_SOCKET_FILEPATH = os.path.join(
    os.getcwd(), 'portserver.socket')
KILL_PORTSERVER_TIMEOUT_SECS = 10

CONSTANT_FILE_PATH = os.path.join(common.CURR_DIR, 'assets', 'constants.ts')
FECONF_FILE_PATH = os.path.join('feconf.py')
WEBDRIVER_HOME_PATH = os.path.join(
    common.NODE_MODULES_PATH, 'webdriver-manager')
WEBDRIVER_MANAGER_BIN_PATH = os.path.join(
    WEBDRIVER_HOME_PATH, 'bin', 'webdriver-manager')

WEBDRIVER_PROVIDER_PATH = os.path.join(
    WEBDRIVER_HOME_PATH, 'dist', 'lib', 'provider')

GECKO_PROVIDER_FILE_PATH = os.path.join(
    WEBDRIVER_PROVIDER_PATH, 'geckodriver.js')

CHROME_PROVIDER_FILE_PATH = os.path.join(
    WEBDRIVER_PROVIDER_PATH, 'chromedriver.js')

CHROME_PROVIDER_BAK_FILE_PATH = os.path.join(
    WEBDRIVER_PROVIDER_PATH, 'chromedriver.js.bak')

GECKO_PROVIDER_BAK_FILE_PATH = os.path.join(
    WEBDRIVER_PROVIDER_PATH, 'geckodriver.js.bak')

WEBPACK_BIN_PATH = os.path.join(
    common.CURR_DIR, 'node_modules', 'webpack', 'bin', 'webpack.js')
PATTERN_FOR_REPLACE_WEBDRIVER_CODE = r'this\.osArch = os\.arch\(\);'
PROTRACTOR_CONFIG_FILE_PATH = os.path.join(
    'core', 'tests', 'protractor.conf.js')
BROWSER_STACK_CONFIG_FILE_PATH = os.path.join(
    'core', 'tests', 'protractor-browserstack.conf.js')

_PARSER = argparse.ArgumentParser(
    description="""
Run this script from the oppia root folder:
   bash scripts/run_e2e_tests.sh

The root folder MUST be named 'oppia'.


  --suite=suite_name Performs test for different suites, here suites are the
        name of the test files present in core/tests/protractor_desktop/ and
        core/test/protractor/ dirs. e.g. for the file
        core/tests/protractor/accessibility.js use --suite=accessibility.
        For performing a full test, no argument is required.
Note: You can replace 'it' with 'fit' or 'describe' with 'fdescribe' to run a
single test or test suite.
""")


_PARSER.add_argument(
    '--skip-install',
    help='If true, skips installing dependencies. The default value is false.',
    action='store_true')
_PARSER.add_argument(
    '--skip-build',
    help='If true, skips building files. The default value is false.',
    action='store_true')
_PARSER.add_argument(
    '--sharding-instances', type=int, default=3,
    help='Sets the number of parallel browsers to open while sharding.'
         'Sharding must be disabled (either by passing in false to --sharding'
         ' or 1 to --sharding-instances) if running any tests in isolation'
         ' (fit or fdescribe).')
_PARSER.add_argument(
    '--prod_env',
    help='Run the tests in prod mode. Static resources are served from'
         ' build directory and use cache slugs.',
    action='store_true')

_PARSER.add_argument(
    '--suite', default='full',
    help='Performs test for different suites, here suites are the'
         'name of the test files present in core/tests/protractor_desktop/ and'
         'core/test/protractor/ dirs. e.g. for the file'
         'core/tests/protractor/accessibility.js use --suite=accessibility.'
         'For performing a full test, no argument is required.')

_PARSER.add_argument(
    '--chrome_driver_version',
    help='Uses the specified version of the chrome driver ')

_PARSER.add_argument(
    '--debug_mode',
    help='Runs the protractor test in debugging mode. Follow the instruction '
         'provided in following URL to run e2e tests in debugging mode: '
         'https://www.protractortest.org/#/debugging#disabled-control-flow',
    action='store_true')

_PARSER.add_argument(
    '--deparallelize_terser',
    help='Disable parallelism on terser plugin in webpack. Use with prod_env.',
    action='store_true')

_PARSER.add_argument(
    '--server_log_level',
    help='Sets the log level for the appengine server. The default value is '
         'set to critical.',
    default='critical',
    choices=['critical', 'error', 'warning', 'info'])

_PARSER.add_argument(
    '--community_dashboard_enabled', action='store_true',
    help='Run the test after enabling the community dashboard page.')

# This list contains the sub process triggered by this script. This includes
# the oppia web server.
SUBPROCESSES = []


def ensure_screenshots_dir_is_removed():
    """Check if screenshot directory exists, if so, delete it."""
    screenshots_dir = os.path.join(os.pardir, 'protractor-screenshots')
    if not os.path.isdir(screenshots_dir):
        return
    python_utils.PRINT(
        'Note: If ADD_SCREENSHOT_REPORTER is set to true in'
        'core/tests/protractor.conf.js, you can view screenshots'
        'of the failed tests in ../protractor-screenshots/')
    os.rmdir(screenshots_dir)


def cleanup():
    """Kill the running subprocesses and server fired in this program, set
    constants back to default values.
    """
    google_app_engine_path = '%s/' % common.GOOGLE_APP_ENGINE_HOME
    webdriver_download_path = '%s/selenium' % WEBDRIVER_HOME_PATH
    if common.is_windows_os():
        # In windows system, the java command line will use absolute path.
        webdriver_download_path = os.path.abspath(webdriver_download_path)
    processes_to_kill = [
        '.*%s.*' % re.escape(google_app_engine_path),
        '.*%s.*' % re.escape(webdriver_download_path)
    ]
    for p in SUBPROCESSES:
        p.kill()

    for p in processes_to_kill:
        common.kill_processes_based_on_regex(p)
    build.set_constants_to_default()


def is_oppia_server_already_running():
    """Check if the ports are taken by any other processes. If any one of
    them is taken, it may indicate there is already one Oppia instance running.

    Returns:
        bool. Whether there is a running Oppia instance.
    """
    running = False
    for port in [OPPIA_SERVER_PORT, GOOGLE_APP_ENGINE_PORT]:
        if common.is_port_open(port):
            python_utils.PRINT(
                'There is already a server running on localhost:%s.'
                'Please terminate it before running the end-to-end tests.'
                'Exiting.' % port)
            running = True
            break
    return running


def run_webpack_compilation():
    """Runs webpack compilation."""
    max_tries = 5
    webpack_bundles_dir_name = 'webpack_bundles'
    for _ in python_utils.RANGE(max_tries):
        try:
            subprocess.check_call([
                common.NODE_BIN_PATH, WEBPACK_BIN_PATH, '--config',
                'webpack.dev.config.ts'])
        except subprocess.CalledProcessError as error:
            python_utils.PRINT(error.output)
            sys.exit(error.returncode)
            return
        if os.path.isdir(webpack_bundles_dir_name):
            break
    if not os.path.isdir(webpack_bundles_dir_name):
        python_utils.PRINT(
            'Failed to complete webpack compilation, exiting ...')
        sys.exit(1)


def run_webdriver_manager(parameters):
    """Run commands of webdriver manager.

    Args:
        parameters: list(str). A list of parameters to pass to webdriver
            manager.
    """
    web_driver_command = [common.NODE_BIN_PATH, WEBDRIVER_MANAGER_BIN_PATH]
    web_driver_command.extend(parameters)
    p = subprocess.Popen(web_driver_command)
    p.communicate()


def update_community_dashboard_status_in_feconf_file(
        feconf_file_path, enable_community_dashboard):
    """Change feconf.py file based on whether the community dashboard is
    enabled.

    Args:
        feconf_file_path: str. Path to the feconf.py file.
        enable_community_dashboard: bool. Represents whether community
            dashboard is enabled.
    """
    pattern = 'COMMUNITY_DASHBOARD_ENABLED = .*'
    replace = 'COMMUNITY_DASHBOARD_ENABLED = %s' % enable_community_dashboard
    common.inplace_replace_file(feconf_file_path, pattern, replace)


def setup_and_install_dependencies(skip_install):
    """Run the setup and installation scripts."""
    if not skip_install:
        install_third_party_libs.main()
    if os.getenv('TRAVIS'):
        install_chrome_on_travis.main(args=[])


def build_js_files(dev_mode_setting, deparallelize_terser=False):
    """Build the javascript files.

    Args:
        dev_mode_setting: bool. Represents whether to run the related commands
            in dev mode.
        deparallelize_terser: bool. Represents whether to use webpack
            compilation config that disables parallelism on terser plugin.
    """
    if not dev_mode_setting:
        python_utils.PRINT('  Generating files for production mode...')
        if deparallelize_terser:
            build.main(args=['--prod_env', '--deparallelize_terser'])
        else:
            build.main(args=['--prod_env'])
    else:
        build.main(args=[])
        run_webpack_compilation()


@contextlib.contextmanager
def tweak_webdriver_manager():
    """webdriver-manager (version 13.0.0) uses `os.arch()` to determine the
    architecture of the operating system, however, this function can only be
    used to determine the architecture of the machine that compiled `node`.
    In the case of Windows, we are using the portable version,
    which was compiled on `ia32` machine so that is the value returned by this
    `os.arch` function. Unfortunately, webdriver-manager seems to assume that
    Windows wouldn't run on the ia32 architecture, so its help function used to
    determine download link returns null for this, which means that the
    application has no idea about where to download the correct version.

    https://github.com/angular/webdriver-manager/blob/b7539a5a3897a8a76abae7245f0de8175718b142/lib/provider/chromedriver.ts#L16
    https://github.com/angular/webdriver-manager/blob/b7539a5a3897a8a76abae7245f0de8175718b142/lib/provider/geckodriver.ts#L21
    https://github.com/angular/webdriver-manager/blob/b7539a5a3897a8a76abae7245f0de8175718b142/lib/provider/chromedriver.ts#L167
    https://github.com/nodejs/node/issues/17036
    """
    try:
        if common.is_windows_os():
            regex_pattern = PATTERN_FOR_REPLACE_WEBDRIVER_CODE
            arch = 'x64' if common.is_x64_architecture() else 'x86'
            replace = 'this.osArch = "%s";' % arch
            common.inplace_replace_file(
                CHROME_PROVIDER_FILE_PATH, regex_pattern, replace)
            common.inplace_replace_file(
                GECKO_PROVIDER_FILE_PATH, regex_pattern, replace)
        yield
    finally:
        if common.is_windows_os():
            undo_webdriver_tweak()


def undo_webdriver_tweak():
    """Undo the tweak on webdriver manager's source code."""
    if os.path.isfile(CHROME_PROVIDER_BAK_FILE_PATH):
        os.remove(CHROME_PROVIDER_FILE_PATH)
        os.rename(CHROME_PROVIDER_BAK_FILE_PATH, CHROME_PROVIDER_FILE_PATH)
    if os.path.isfile(GECKO_PROVIDER_BAK_FILE_PATH):
        os.remove(GECKO_PROVIDER_FILE_PATH)
        os.rename(GECKO_PROVIDER_BAK_FILE_PATH, GECKO_PROVIDER_FILE_PATH)


def start_webdriver_manager(version):
    """Update and start webdriver manager.

    Args:
        version: str. The chromedriver version.
    """
    with tweak_webdriver_manager():
        run_webdriver_manager(
            ['update', '--versions.chrome', version])
        run_webdriver_manager(
            ['start', '--versions.chrome', version,
             '--detach', '--quiet'])


def get_parameter_for_sharding(sharding_instances):
    """Return the parameter for sharding, based on the given number of
    sharding instances.

    Args:
        sharding_instances: int. How many sharding instances to be running.

    Returns:
        list(str). A list of parameters to represent the sharding configuration.
    """
    if sharding_instances <= 0:
        raise ValueError('Sharding instance should be larger than 0')
    if sharding_instances == 1:
        return []
    else:
        return ['--capabilities.shardTestFiles=True',
                '--capabilities.maxInstances=%s' % sharding_instances]


def get_parameter_for_dev_mode(dev_mode_setting):
    """Return parameter for whether the test should be running on dev_mode.

    Args:
        dev_mode_setting: bool. Whether the test is running on dev_mode.

    Returns:
        str. A string for the testing mode command line parameter.
    """
    return '--params.devMode=%s' % dev_mode_setting


def get_parameter_for_suite(suite_name):
    """Return a parameter for which suite to run the tests for.

    Args:
        suite_name: str. The suite name whose tests should be run. If the value
            is `full`, all tests will run.

    Returns:
        list(str). A list of command line parameters for the suite.
    """
    return ['--suite', suite_name]


def get_e2e_test_parameters(
        sharding_instances, suite_name, dev_mode_setting):
    """Return parameters for the end-2-end tests.

    Args:
        sharding_instances: str. Sets the number of parallel browsers to open
            while sharding.
        suite_name: str. Performs test for different suites.
        dev_mode_setting: bool. Represents whether run the related commands in
            dev mode.

    Returns:
        list(str). Parameters for running the tests.
    """
    sharding_parameters = get_parameter_for_sharding(sharding_instances)
    dev_mode_parameters = get_parameter_for_dev_mode(dev_mode_setting)
    suite_parameter = get_parameter_for_suite(suite_name)

    commands = [PROTRACTOR_CONFIG_FILE_PATH]
    commands.extend(sharding_parameters)
    commands.extend(suite_parameter)
    commands.append(dev_mode_parameters)

    return commands


def start_google_app_engine_server(dev_mode_setting, log_level):
    """Start the Google App Engine server.

    Args:
        dev_mode_setting: bool. Represents whether to run the related commands
            in dev mode.
        log_level: str. The log level for the google app engine server.
    """
    app_yaml_filepath = 'app%s.yaml' % ('_dev' if dev_mode_setting else '')

    p = subprocess.Popen(
        '%s %s/dev_appserver.py --host 0.0.0.0 --port %s '
        '--clear_datastore=yes --dev_appserver_log_level=%s '
        '--log_level=%s --skip_sdk_update_check=true %s' % (
            common.CURRENT_PYTHON_BIN, common.GOOGLE_APP_ENGINE_HOME,
            GOOGLE_APP_ENGINE_PORT, log_level, log_level, app_yaml_filepath),
        env={'PORTSERVER_ADDRESS': PORTSERVER_SOCKET_FILEPATH},
        shell=True)
    SUBPROCESSES.append(p)


def get_chrome_driver_version():
    """Fetches the latest supported version of chromedriver depending on the
    Chrome version.
    This method follows the steps mentioned here:
    https://chromedriver.chromium.org/downloads/version-selection
    """
    popen_args = ['google-chrome', '--version']
    if common.is_mac_os():
        # There are spaces between Google and Chrome in the path. Spaces don't
        # need to be escaped when we're not using the terminal, ie. shell=False
        # for Popen by default.
        popen_args = [
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            '--version'
        ]
    try:
        proc = subprocess.Popen(popen_args, stdout=subprocess.PIPE)
        output = proc.stdout.readline()
    except OSError:
        # For the error message for the mac command, we need to add the
        # backslashes in. This is because it is likely that a user will try to
        # run the command on their terminal and, as mentioned above, the mac
        # get chrome version command has spaces in the path which need to be
        # escaped for successful terminal use.
        raise Exception(
            'Failed to execute "%s" command. '
            'This is used to determine the chromedriver version to use. '
            'Please set the chromedriver version manually using '
            '--chrome_driver_version flag. To determine the chromedriver '
            'version to be used, please follow the instructions mentioned '
            'in the following URL:\n'
            'https://chromedriver.chromium.org/downloads/version-selection' % (
                ' '.join(arg.replace(' ', r'\ ') for arg in popen_args)
            )
        )
    chrome_version = ''.join(re.findall(r'([0-9]|\.)', output))
    chrome_version = '.'.join(chrome_version.split('.')[:-1])
    response = python_utils.url_open(
        'https://chromedriver.storage.googleapis.com/LATEST_RELEASE_%s'
        % chrome_version)
    chrome_driver_version = response.read()
    python_utils.PRINT('\n\nCHROME VERSION: %s' % chrome_version)
    return chrome_driver_version


def start_portserver():
    """Start a portserver in a subprocess.

    The portserver listens at PORTSERVER_SOCKET_FILEPATH and allocates free
    ports to clients. This prevents race conditions when two clients
    request ports in quick succession. The local Google App Engine
    server that we use to serve the development version of Oppia uses
    python_portpicker, which is compatible with the portserver this
    function starts, to request ports.

    By "compatible" we mean that python_portpicker requests a port by
    sending a request consisting of the PID of the requesting process
    and expects a response consisting of the allocated port number. This
    is the interface provided by this portserver.

    Returns:
        subprocess.Popen. The Popen subprocess object.
    """
    process = subprocess.Popen([
        'python', '-m',
        '.'.join(['scripts', 'run_portserver']),
        '--portserver_unix_socket_address',
        PORTSERVER_SOCKET_FILEPATH,
    ])
    return process


def cleanup_portserver(portserver_process):
    """Shut down the portserver.

    We wait KILL_PORTSERVER_TIMEOUT_SECS seconds for the portserver to
    shut down after sending CTRL-C (SIGINT). The portserver is configured
    to shut down cleanly upon receiving this signal. If the server fails
    to shut down, we kill the process.

    Args:
        portserver_process: subprocess.Popen. The Popen subprocess
            object for the portserver.
    """
    portserver_process.send_signal(signal.SIGINT)
    for _ in python_utils.RANGE(KILL_PORTSERVER_TIMEOUT_SECS):
        time.sleep(1)
        if not portserver_process.poll():
            break
    if portserver_process.poll():
        portserver_process.kill()


def main(args=None):
    """Run the scripts to start end-to-end tests."""

    parsed_args = _PARSER.parse_args(args=args)
    oppia_instance_is_already_running = is_oppia_server_already_running()

    if oppia_instance_is_already_running:
        sys.exit(1)
    setup_and_install_dependencies(parsed_args.skip_install)

    atexit.register(cleanup)

    dev_mode = not parsed_args.prod_env

    update_community_dashboard_status_in_feconf_file(
        FECONF_FILE_PATH, parsed_args.community_dashboard_enabled)

    if parsed_args.skip_build:
        build.modify_constants(prod_env=parsed_args.prod_env)
    else:
        build_js_files(
            dev_mode, deparallelize_terser=parsed_args.deparallelize_terser)
    version = parsed_args.chrome_driver_version or get_chrome_driver_version()
    python_utils.PRINT('\n\nCHROMEDRIVER VERSION: %s\n\n' % version)
    start_webdriver_manager(version)

    portserver_process = start_portserver()
    atexit.register(cleanup_portserver, portserver_process)
    start_google_app_engine_server(dev_mode, parsed_args.server_log_level)

    common.wait_for_port_to_be_open(WEB_DRIVER_PORT)
    common.wait_for_port_to_be_open(GOOGLE_APP_ENGINE_PORT)
    ensure_screenshots_dir_is_removed()
    commands = [common.NODE_BIN_PATH]
    if parsed_args.debug_mode:
        commands.append('--inspect-brk')
    # This flag ensures tests fail if waitFor calls time out.
    commands.append('--unhandled-rejections=strict')
    commands.append(PROTRACTOR_BIN_PATH)
    commands.extend(get_e2e_test_parameters(
        parsed_args.sharding_instances, parsed_args.suite, dev_mode))

    p = subprocess.Popen(commands)
    p.communicate()
    sys.exit(p.returncode)


if __name__ == '__main__':  # pragma: no cover
    main()
