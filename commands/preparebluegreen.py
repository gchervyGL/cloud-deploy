"""Classes pertaining to blue/green preparation."""
from fabric.colors import green as _green, yellow as _yellow, red as _red

from ghost_log import log
from ghost_aws import check_autoscale_exists, update_auto_scale
from ghost_aws import create_launch_config, generate_userdata
from ghost_tools import GCallException, get_aws_connection_data, get_app_friendly_name, get_app_module_name_list
from settings import cloud_connections, DEFAULT_PROVIDER
from libs.blue_green import get_blue_green_apps, check_app_manifest, get_blue_green_config
from libs.autoscaling import get_instances_from_autoscaling, get_autoscaling_group_object
from libs.deploy import get_path_from_app_with_color
from libs.elb import get_elb_from_autoscale, copy_elb, register_elb_into_autoscale

COMMAND_DESCRIPTION = "Prepare the Blue/Green env before swap"


class Preparebluegreen(object):
    """Checks and prepares blue/green deployment before swap."""

    _app = None
    _job = None
    _log_file = -1

    def __init__(self, worker):
        """init from worker attributes."""
        self._app = worker.app
        self._job = worker.job
        self._db = worker._db
        self._config = worker._config
        self._worker = worker
        self._log_file = worker.log_file
        self._connection_data = get_aws_connection_data(
            self._app.get('assumed_account_id', ''),
            self._app.get('assumed_role_name', ''),
            self._app.get('assumed_region_name', '')
        )
        self._cloud_connection = cloud_connections.get(self._app.get('provider', DEFAULT_PROVIDER))(
            self._log_file,
            **self._connection_data
        )

    def _get_notification_message_failed(self, online_app, offline_app, e):
        app_name = get_app_friendly_name(online_app)
        notif = "Blue/green preparation failed for [{0}] between [{1}] and [{2}]: {3}".format(app_name, online_app['_id'], offline_app['_id'], str(e))
        return _red(notif)

    def _get_notification_message_aborted(self, app, msg):
        notif = "Blue/green preparation aborted for [{0}] : {1}".format(get_app_friendly_name(app), msg)
        return _yellow(notif)

    def _get_notification_message_done(self, app, elb_name, elb_dns):
        app_name = get_app_friendly_name(app)
        as_name = app['autoscale']['name']
        notif = "Blue/green preparation done for [{0}] by creating the temporary ELB [{1}/{2}] attached to the AutoScale '{3}'".format(app_name, elb_name, elb_dns, as_name)
        return _green(notif)

    def _update_app_autoscale_options(self, app, ref_app, log_file):
        """ Updates the App DB object to set the 'autoscale' attribute.
        """
        self._worker._db.apps.update({ '_id': app['_id']}, {'$set': {
            'autoscale.min': ref_app['autoscale']['min'],
            'autoscale.max': ref_app['autoscale']['max'],
            'autoscale.current': ref_app['autoscale']['current']
        }})
        log(_green("'{0}' autoscale has been update '{1}'".format(app['_id'], app['autoscale']['name'])), log_file)

    def execute(self):
        """Execute all checks and preparations."""
        log(_green("STATE: Started"), self._log_file)
        copy_ami_option = self._job['options'][0] if 'options' in self._job and len(self._job['options']) > 0 else get_blue_green_config(self._config, 'preparebluegreen', 'copy_ami', False)

        app_region = self._app['region']
        as_conn = self._cloud_connection.get_connection(app_region, ["ec2", "autoscale"])
        as_conn3 = self._cloud_connection.get_connection(app_region, ['autoscaling'], boto_version='boto3')

        online_app, offline_app = get_blue_green_apps(self._app,
                                                      self._worker._db.apps,
                                                      self._log_file)

        try:
            # check if app is online
            if not online_app:
                self._worker.update_status("aborted", message=self._get_notification_message_aborted(self._app, "Blue/green is not enabled on this app or not well configured"))
                return

            # Check if app has up to date AMI
            if ((not copy_ami_option and 'ami' not in offline_app) or
                (copy_ami_option and 'ami' not in online_app)):
                self._worker.update_status("aborted", message=self._get_notification_message_aborted(offline_app, "Please run `Buildimage` first or use the `copy_ami` option"))
                return

            # Check if app has AS
            if offline_app['autoscale']['name'] and online_app['autoscale']['name']:
                if not (check_autoscale_exists(self._cloud_connection, offline_app['autoscale']['name'], offline_app['region'])
                    and check_autoscale_exists(self._cloud_connection, online_app['autoscale']['name'], online_app['region'])):
                    self._worker.update_status("aborted", message=self._get_notification_message_aborted(offline_app, "Please check that the configured AutoScale on both green and blue app exists."))
                    return
            else:
                self._worker.update_status("aborted", message=self._get_notification_message_aborted(offline_app, "Please set an AutoScale on both green and blue app."))
                return

            # Check if we have two different AS !
            if offline_app['autoscale']['name'] == online_app['autoscale']['name']:
                self._worker.update_status("aborted", message=self._get_notification_message_aborted(offline_app, "Please set a different AutoScale on green and blue app."))
                return

            if copy_ami_option:
                log("Copy AMI option activated. AMI used by [{0}] will be reused by [{1}]".format(online_app['autoscale']['name'], offline_app['autoscale']['name']), self._log_file)

            # Check if modules have been deployed
            if get_blue_green_config(self._config, 'preparebluegreen', 'module_deploy_required', False):
                if not check_app_manifest(offline_app, self._config, self._log_file):
                    self._worker.update_status("aborted", message=self._get_notification_message_aborted(offline_app, "Please deploy your app's modules"))
                    return

            # Check if instances are already running
            if get_instances_from_autoscaling(offline_app['autoscale']['name'], as_conn3):
                self._worker.update_status("aborted", message=self._get_notification_message_aborted(offline_app, "Autoscaling Group of offline app should be empty."))
                return

            # Get the online ELB
            online_elbs = get_elb_from_autoscale(online_app['autoscale']['name'], as_conn)
            if len(online_elbs) == 0:
                self._worker.update_status("aborted", message=self._get_notification_message_aborted(offline_app, "Online app AutoScale is not attached to a valid Elastic Load Balancer"))
                return

            # Create the temporary ELB: ghost-bluegreentemp-{original ELB name}, duplicated from the online ELB
            elb_conn3 = self._cloud_connection.get_connection(app_region, ['elb'], boto_version='boto3')
            online_elb = online_elbs[0]
            temp_elb_name = "bgtmp-{0}".format(offline_app['_id'])[:31] # ELB name is 32 char long max
            log(_green("Creating the temporary ELB [{0}] by copying parameters from [{1}]".format(temp_elb_name, online_elb)), self._log_file)
            new_elb_dns = copy_elb(elb_conn3, temp_elb_name, online_elb, {'Key': 'app_id', 'Value': str(offline_app['_id'])})

            # Register the temporary ELB into the AutoScale
            log(_green("Attaching ELB [{0}] to the AutoScale [{1}]".format(temp_elb_name, offline_app['autoscale']['name'])), self._log_file)
            register_elb_into_autoscale(offline_app['autoscale']['name'], as_conn3, [], [temp_elb_name], self._log_file)

            offline_app['autoscale']['min'] = online_app['autoscale']['min']
            offline_app['autoscale']['max'] = online_app['autoscale']['max']
            online_asg_object = get_autoscaling_group_object(as_conn3, online_app['autoscale']['name'])
            offline_app['autoscale']['current'] = online_asg_object['DesiredCapacity']
            if copy_ami_option:
                offline_app['ami'] = online_app['ami']
                offline_app['build_infos']['ami_name'] = online_app['build_infos']['ami_name']
            # Update AutoScale properties in DB App
            self._update_app_autoscale_options(offline_app, online_app, self._log_file)
            # Update AutoScale properties and starts instances
            launch_config = None
            if copy_ami_option:
                userdata = generate_userdata(self._config['bucket_s3'], self._config.get('bucket_region', self._app['region']), self._config['ghost_root_path'])
                if userdata:
                    launch_config = create_launch_config(self._cloud_connection, offline_app, userdata, offline_app['ami'])

            update_auto_scale(self._cloud_connection, offline_app, launch_config, self._log_file, update_as_params=True)
            log(_green("Starting [{0}] instance(s) into the AutoScale [{1}]".format(offline_app['autoscale']['current'], offline_app['autoscale']['name'])), self._log_file)

            self._worker.update_status("done", message=self._get_notification_message_done(offline_app, temp_elb_name, new_elb_dns))
        except GCallException as e:
            self._worker.update_status("failed", message=self._get_notification_message_failed(online_app, offline_app, e))
