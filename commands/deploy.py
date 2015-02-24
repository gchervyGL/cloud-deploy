import os
import sys
import datetime
import calendar
import time
import shutil
import tempfile
from sh import git
from pymongo import MongoClient
from commands.tools import GCallException, gcall, log
from commands.initrepo import InitRepo
from boto.ec2 import autoscale

ROOT_PATH = os.path.dirname(os.path.realpath(__file__))

class Deploy():
    _app = None
    _job = None
    _log_file = -1
    _app_path = None
    _git_repo = None
    _dry_run = None
    _as_conn = None
    _as_group = None
    _worker = None

    def __init__(self, worker):
        self._app = worker.app
        self._job = worker.job
        self._log_file = worker.log_file
        self._config = worker._config
        self._worker = worker
        # FIXME Deal with multiple job modules.
        # Deal only with first (0) job module for now

    def _find_modules_by_name(self, modules):
        for module in modules:
            if 'name' in module:
                for item in self._app['modules']:
                    if 'name' in item and item['name'] == module['name']:
                        yield item

    def _get_path_from_module(self, module):
        return "/ghost/{name}/{env}/{role}/{module}".format(name=self._app['name'], env=self._app['env'], role=self._app['role'], module=module['name'])

    def _initialize_module(self, module):
        path = self._get_path_from_module(module)
        try:
            shutil.rmtree(path)
        except (OSError, IOError) as e:
            print(e)
        try:
            os.makedirs(path)
        except:
            raise GCallException("Init module: {0} failed, creating directory".format(module['name']))
        os.chdir(path)
        gcall("git clone {git_repo} {path}".format(git_repo=module['git_repo'], path=path), "Git clone", self._log_file)
        self._worker.module_initialized(module['name'])

    def _predeploy_module(self, module):
        """
        Execute tasks before packaging application (ie: install lib dependencies)
        """
        #predeploy = os.path.join(ROOT_PATH, 'predeploy', 'symfony_predeploy.sh')
        #shutil.copy(predeploy, self._app_path)
        #os.chdir(self._app_path)
        #gcall('./symfony_predeploy.sh %s' % self._app['env'], 'Predeploy script')
        pass

    def _postdeploy_module(self, module):
        """
        Execute tasks after deployment (ie: clear cache)
        """
        #postdeploy = os.path.join(ROOT_PATH, 'postdeploy', 'symfony_postdeploy.sh')
        #shutil.copy(postdeploy, self._app_path)
        #os.chdir(self._app_path)
        #gcall('./symfony_postdeploy.sh %s' % self._app['env'], 'Postdeploy script')
        pass

    def _set_as_conn(self):
        self._as_conn = autoscale.connect_to_region(self._app['aws_region'])

    def _set_autoscale_group(self):
        if not self._as_conn:
            self._set_as_conn()
        if 'autoscale' in self._app.keys():
            if 'name' in self._app['autoscale'].keys():
                self._as_group = self._as_conn.get_all_groups(names=self._app['autoscale']['name'])

    def _start_autoscale(self):
        if not self._as_group:
            self._set_autoscale_group()
        if (self._as_group):
            log("Resuming autoscaling", self._log_file)
            self._as_conn.resume_processes(self._as_group)

    def _stop_autoscale(self):
        if not self._as_group:
            self._set_autoscale_group()
        if (self._as_group):
            log("Stopping autoscaling", self._log_file)
            self._as_conn.suspend_processes(self._as_group)

    def _sync_instances(self, task_name):
        os.chdir(ROOT_PATH)
        cmd = "/usr/local/bin/fab -i {key_path} set_hosts:ghost_app={app},ghost_env={env},ghost_role={role},region={aws_region} {0}".format(task_name, \
                key_path=self._config['key_path'], app=self._app['name'], env=self._app['env'], role=self._app['role'], aws_region=self._app['aws_region'])
        gcall(cmd, "Updating current instances", self._log_file)

    def _package_module(self, module, ts, commit):
        os.chdir(self._get_path_from_module(module))
        pkg_name = "{0}_{1}_{2}.tar.gz".format(ts, module['name'], commit)
        gcall("tar cvzf ../%s . > /dev/null" % pkg_name, "Creating package: %s" % pkg_name, self._log_file)
        gcall("aws s3 cp ../{0} s3://{bucket_s3}{path}/".format(pkg_name, \
                bucket_s3=self._config['bucket_s3'], path=self._get_path_from_module(module)), "Uploading package: %s" % pkg_name, self._log_file)
        return pkg_name

    def _purge_package(self, pkg_name):
        task_name = "purge:{0}".format(pkg_name)
        gcall("/usr/local/bin/fab -i {key_path} set_hosts:ghost_app={app},ghost_env={env},ghost_role={role},region={aws_region} {0}".format(task_name, **self._app), "Purging package: %s" % pkg_name)

    def _get_module_revision(self, module_name):
        for module in self._job['modules']:
            if 'name' in module and module['name'] == module_name:
                if 'rev' in module:
                    return module['rev']
                return 'master'

    def execute(self):
        try:
            self._apps_modules = self._find_modules_by_name(self._job['modules'])
            for module in self._apps_modules:
                if not module['initialized']:
                    self._initialize_module(module)

            self._apps_modules = self._find_modules_by_name(self._job['modules'])
            for module in self._apps_modules:
                self._execute_deploy(module)

            self._worker.update_status("done", message="Deployment OK")
        except GCallException as e:
            self._worker.update_status("failed", message=str(e))


    def _execute_deploy(self, module):
        """
        0) Update sourcecode
        1) Stop Autoscaling
        2) Update MANIFEST on S3
        3) Deploy package on Running EC2 instances
        4) Restart Webserver
        5) Start Autoscaling
        """
        now = datetime.datetime.utcnow()
        ts = calendar.timegm(now.timetuple())
        os.chdir(self._get_path_from_module(module))
        gcall("git clean -f", "Reseting git repository", self._log_file)
        gcall("git pull", "Git pull", self._log_file)
        revision = self._get_module_revision(module['name'])
        gcall("git checkout %s" % revision, "Git checkout: %s" % revision, self._log_file)
        commit = git('rev-parse', '--short', 'HEAD').strip()
        # FIXME execute predeploy
        print('pre deploy')
        self._predeploy_module(module)
        # FIXME execute buildpack
        print('execute buildpack')
        pkg_name = self._package_module(module, ts, commit)
        manifest, manifest_path = tempfile.mkstemp()
        if sys.version > '3':
            os.write(manifest, bytes(pkg_name, 'UTF-8'))
        else:
            os.write(manifest, pkg_name)
        self._set_as_conn()
        self._stop_autoscale()
        gcall("aws s3 cp {0} s3://{bucket_s3}{path}/MANIFEST".format(manifest_path, \
                bucket_s3=self._config['bucket_s3'], path=self._get_path_from_module(module)), "Uploading manifest", self._log_file)
        self._sync_instances('deploy')
        os.close(manifest)
        self._start_autoscale()
        # FIXME execute postdeploy
        print('post deploy')
        #self._app = self._db.apps.find_one({'_id': self._app['_id']})
        #if len(self._app['deploy']) > 3:
        #    pkg_timestamped = self._app['deploy'][0].split('_')[0]
        #    self._purge_package(pkg_timestamped)
        #    self._db.apps.update({'_id': self._app['_id']}, { '$pop': {'deploy': -1} })
        self._purge_old_modules(module)
        deployment = {'app_id': self._app['_id'], 'job_id': self._job['_id'], 'module': module['name'], 'commit': commit, 'timestamp': ts}
        self._worker._db.deploy_histories.insert(deployment)

    def finish():
        pass
