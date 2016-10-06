"""Library pertaining to blue/green commands."""
# -*- coding: utf-8 -*-
#!/usr/bin/env python

import sys
from ghost_log import log
from ghost_tools import config
from .deploy import get_path_from_app_with_color
from settings import cloud_connections, DEFAULT_PROVIDER

BLUE_GREEN_COMMANDS = ['preparebluegreen', 'swapbluegreen', 'purgebluegreen']

def ghost_has_blue_green_enabled():
    """
    Return if Ghost has Blue/Green option enabled globally
    """
    return config.get('blue_green') and config.get('blue_green').get('enabled', False)

def get_blue_green_config(config, command, key, default_value):
    """
    Return the Blue Green command option from global config
    """
    blue_green_section = config.get('blue_green', None)
    if not blue_green_section:
        return default_value
    command_section = blue_green_section.get(command, None)
    if not command_section:
        return default_value
    return command_section.get(key, default_value)

def get_blue_green_destroy_temporary_elb_config(config):
    return get_blue_green_config(config, 'purgebluegreen', 'destroy_temporary_elb', True)

def get_blue_green_from_app(app):
    """
    Returns the blue_green object if exists and it's color field if exists

    >>> get_blue_green_from_app({})
    (None, None)

    >>> get_blue_green_from_app({'blue_green': None})
    (None, None)

    >>> get_blue_green_from_app({'blue_green': {}})
    (None, None)

    >>> get_blue_green_from_app({'blue_green': {'color': None}})
    ({'color': None}, None)

    >>> get_blue_green_from_app({'blue_green': {'color': ''}})
    ({'color': ''}, '')

    >>> get_blue_green_from_app({'blue_green': {'color': 'blue'}})
    ({'color': 'blue'}, 'blue')

    >>> get_blue_green_from_app({'blue_green': {'color': 'green'}})
    ({'color': 'green'}, 'green')
    """
    if app.get('blue_green'):
        return app['blue_green'], app['blue_green'].get('color', None)
    return None, None

def get_blue_green_apps(app, apps_db, log_file):
    """
    Return app and alter_ego_app if at least one is online.

    Online app is returned first.
    """
    if app.get('blue_green') and app['blue_green'].get('alter_ego_id'):
        alter_ego_app = apps_db.find_one(
            {'_id': app['blue_green']['alter_ego_id']}
        )
        # Both app online is inconsistent
        if app['blue_green']['is_online'] and alter_ego_app['blue_green']['is_online']:
            log("ERROR: Both blue ({0}) and green ({1}) app are setted as 'online' which is not possible.".format(app['_id'], alter_ego_app['_id']), log_file)
            return None, None
        if app['blue_green']['is_online']:
            return app, alter_ego_app
        else:
            if alter_ego_app['blue_green']['is_online']:
                return alter_ego_app, app
            else:
                log("ERROR: Nor blue ({0}) and green ({1}) app are setted as 'online'".format(app['_id'], alter_ego_app['_id']), log_file)
                return None, None
    else:
        return None, None

def check_app_manifest(app, config, log_file):
    key_path = get_path_from_app_with_color(app) + '/MANIFEST'
    cloud_connection = cloud_connections.get(app.get('provider', DEFAULT_PROVIDER))(log_file)
    conn = cloud_connection.get_connection(config.get('bucket_region', app['region']), ["s3"])
    bucket = conn.get_bucket(config['bucket_s3'])
    key = bucket.get_key(key_path)
    if not key:
        return False
    manifest = key.get_contents_as_string()
    if sys.version > '3':
        manifest = manifest.decode('utf-8')
    nb_deployed_modules = len(manifest.strip().split('\n'))
    nb_app_modules = len(app['modules'])
    return nb_deployed_modules == nb_app_modules
