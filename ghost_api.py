import copy

OPPOSITE_COLOR = {
    'blue': 'green',
    'green': 'blue'
}

def ghost_api_bluegreen_is_enabled(blue_green):
    return blue_green and blue_green.get('enable_blue_green', None)

def ghost_api_check_green_app_exists(apps_db, app):
    name = app.get('name')
    role = app.get('role')
    env = app.get('env')
    blue_green = app.get('blue_green')
    color = blue_green.get('color', 'blue') if blue_green else 'blue'
    green_app = apps_db.find_one({'$and' : [
        {'name': name},
        {'role': role},
        {'env': env},
        {'blue_green.color': OPPOSITE_COLOR[color]}
    ]})
    return green_app

def ghost_api_create_green_app(apps_db, app, user):
    # Generate the BlueScreen object for the green app
    blue_green_source = app.get('blue_green')
    color = blue_green_source.get('color', 'blue') if blue_green_source else 'blue'
    blue_green = {
        'enable_blue_green': True,
        'color': OPPOSITE_COLOR[color],
        'is_online': False,
        'alter_ego_id': app['_id']
    }

    # Create the green app and return its ID
    green_app = copy.copy(app)
    green_app['user'] = user
    green_app['blue_green'] = blue_green
    del green_app['_id']
    del green_app['_etag']
    del green_app['_version']
    del green_app['_created']
    del green_app['_updated']

    greep_app_id = apps_db.insert_one(green_app).inserted_id
    return greep_app_id

def ghost_api_update_bluegreen_app(apps_db, blue_app, green_app_id):
    # Generate the BlueScreen object for the green app
    blue_green = blue_app.get('blue_green')
    color = blue_green.get('color') if blue_green else 'blue'
    blue_green = {
        'enable_blue_green': True,
        'color': color,
        'is_online': True,
        'alter_ego_id': green_app_id
    }
    return apps_db.update_one({ '_id': blue_app['_id']}, {'$set': { 'blue_green': blue_green }})

def ghost_api_enable_green_app(apps_db, app, user):
    green_app = ghost_api_check_green_app_exists(apps_db, app)
    if not green_app:
        green_app_id = ghost_api_create_green_app(apps_db, app, user)
        if not green_app_id:
            return False
        else:
            return ghost_api_update_bluegreen_app(apps_db, app, green_app_id)
    else:
        return ghost_api_update_bluegreen_app(apps_db, app, green_app['_id'])
