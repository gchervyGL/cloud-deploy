import env
import code_deploy
import instance_role
import salt_features
import aws_data

apps_schema = {
    # TODO required filds
    'name' : {'type': 'string', 'regex': '^[a-zA-Z0-9_.+-]*$'},
    'aws_region' : {'type': 'string', 'allowed':['us-east-1','eu-weast-1']},
    'instance_type': {'type': 'string', 'allowed':aws_data.instance_type},
    'env': {'type': 'string', 'allowed':env.env},
    'features':{'type': 'list', 'allowed':salt_features.recipes},
    'role' : {'type': 'string', 'allowed':instance_role.role},
    'ami': {'type': 'string'},
    'vpc': {'type': 'string'},
    'modules': {'type':'list','schema':{
        'name': {'type':'string'},
        'git_repo' : {'type':'string'},
        'scope' : {'type':'string', 'allowed':['system','code']},
        'code_deploy' : {'type':'dict', 'schema':code_deploy.code_deploy},
        'build_pack':{'type':'media'}}
    },
    'log_notifications' : {'type':'list','items':[{'type':'string',
        'regex':'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'}]
    },
    'autoscale': { 'type': 'dict', 'schema': {
        '_min': {'type':'integer', 'min':0},
        '_max': {'type':'integer', 'min':1},
        'current': {'type':'integer'}
        }
    },
    # TODO solve dynamic schema (rds-mysql, ec-redis, ec-memcached...)
    # TODO solve storing password in cleartext
    'ressources': {'type':'list', 'schema': {
        '_type': { 'type': 'string', 'allowed':['rds-mysql'] },
        'hostname': {'type': 'string'},
        'database': {'type': 'string'},
        'login': {'type': 'string'},
        'password': {'type': 'string'}}
    }
}

apps = {
'item_title' : 'app',
'schema' : apps_schema
}


#def pre_GET_apps(request, lookup):
#    print 'A GET request on apps endpoint has just been received!'
