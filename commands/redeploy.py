import os
import sys
import tempfile
import boto.s3
from bson.objectid import ObjectId
from ghost_tools import GCallException, log, deploy_module_on_hosts

class Redeploy():
    _app = None
    _job = None
    _log_file = -1
    _config = None

    def __init__(self, worker):
        self._app = worker.app
        self._job = worker.job
        self._db = worker._db
        self._worker = worker
        self._config = worker._config
        self._log_file = worker.log_file

    def _find_modules_by_name(self, modules):
        result = []
        for module in modules:
            if 'name' in module:
                for item in self._app['modules']:
                    if 'name' in item and item['name'] == module['name']:
                        result.append(item)
        return result

    def _get_path_from_app(self):
        return "/ghost/{name}/{env}/{role}".format(name=self._app['name'], env=self._app['env'], role=self._app['role'])

    def _update_manifest(self, module, package):
        key_path = self._get_path_from_app() + '/MANIFEST'
        conn = boto.s3.connect_to_region(self._config.get('bucket_region', self._app['region']))
        bucket = conn.get_bucket(self._config['bucket_s3'])
        key = bucket.get_key(key_path)
        modules = []
        module_exist = False
        data = ""
        if key:
            manifest = key.get_contents_as_string()
            if sys.version > '3':
                manifest = manifest.decode('utf-8')
            for line in manifest.split('\n'):
                if line:
                    mod = {}
                    tmp = line.split(':')
                    mod['name'] = tmp[0]
                    if mod['name'] == module['name']:
                        mod['package'] = package
                        mod['path'] = module['path']
                        module_exist = True
                    else:
                        mod['package'] = tmp[1]
                        mod['path'] = tmp[2]
                    modules.append(mod)
        if not key:
            key = bucket.new_key(key_path)
        if not module_exist:
            modules.append({ 'name': module['name'], 'package': package, 'path': module['path']})
        for mod in modules:
            data = data + mod['name'] + ':' + mod['package'] + ':' + mod['path'] + '\n'
        manifest, manifest_path = tempfile.mkstemp()
        if sys.version > '3':
            os.write(manifest, bytes(data, 'UTF-8'))
        else:
            os.write(manifest, data)
        os.close(manifest)
        key.set_contents_from_filename(manifest_path)

    def _get_deploy_infos(self, deploy_id):
        deploy_infos = self._db.deploy_histories.find_one({'_id': ObjectId(deploy_id)})
        if deploy_infos:
            module = {}
            module['path'] = deploy_infos['module_path']
            module['name'] = deploy_infos['module']
            return module, deploy_infos['package']
        return None, None

    def _deploy_module(self, module, fabric_execution_strategy):
        deploy_module_on_hosts(module, fabric_execution_strategy, self._app, self._config, self._log_file)

    def _execute_redeploy(self, deploy_id, fabric_execution_strategy):
        module, package = self._get_deploy_infos(deploy_id)
        if module and package:
            self._update_manifest(module, package)
            self._deploy_module(module, fabric_execution_strategy)
        else:
            raise GCallException("Redeploy on deployment ID: {0} failed".format(deploy_id))

    def execute(self):
        log("Redeploying module", self._log_file)
        if 'options' in self._job and len(self._job['options']) > 0:
            deploy_id = self._job['options'][0]
            fabric_execution_strategy = self._job['options'][1] if len(self._job['options']) > 0 else None
            try:
                self._execute_redeploy(deploy_id, fabric_execution_strategy)
                self._worker.update_status("done", message="Redeploy OK: [{0}]".format(deploy_id))
            except GCallException as e:
                self._worker.update_status("failed", message="Redeploy Failed: [{0}]\n{1}".format(deploy_id, str(e)))
        else:
            self._worker.update_status("failed", message="Incorrect job request: missing options field deploy_id")