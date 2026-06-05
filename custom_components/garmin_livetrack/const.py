from datetime import timedelta
from homeassistant.const import Platform
DOMAIN='garmin_livetrack'
PLATFORMS=[Platform.SENSOR,Platform.BINARY_SENSOR,Platform.DEVICE_TRACKER]
EVENT_IMAP_CONTENT='imap_content'
EVENT_SESSION_ADDED='garmin_livetrack_session_added'
EVENT_SESSION_UPDATED='garmin_livetrack_session_updated'
EVENT_SESSION_ENDED='garmin_livetrack_session_ended'
EVENT_SESSION_REJECTED='garmin_livetrack_session_rejected'
EVENT_POINT_RECEIVED='garmin_livetrack_point_received'
SERVICE_ADD_URL='add_url'
SERVICE_STOP_SESSION='stop_session'
SERVICE_CLEAR_ENDED='clear_ended'
SERVICE_RELOAD_USERS='reload_users'
SERVICE_REFRESH_SESSION='refresh_session'
SERVICE_REFRESH_ALL='refresh_all'
SERVICE_CLEANUP_LEGACY_ENTITIES='cleanup_legacy_entities'
SERVICE_SET_USER_POLICY='set_user_policy'
SERVICE_REMOVE_USER='remove_user'
SERVICE_LIST_USERS='list_users'
CONF_LISTEN_TO_IMAP_EVENTS='listen_to_imap_events'
CONF_STRICT_USERS='strict_users'
CONF_ACCEPT_FIRST_SEEN_USERS='accept_first_seen_users'
CONF_ALLOWED_USERS='allowed_users'
CONF_USER_POLICIES='user_policies'
CONF_ACTIVITY_FILTER='activity_filter'
CONF_UPDATE_PROFILE='update_profile'
CONF_UPDATE_INTERVAL='update_interval_seconds'
CONF_USE_GARMIN_TRACKPOINT_FREQUENCY='use_garmin_trackpoint_frequency'
CONF_INITIAL_TRACKPOINT_WAIT='initial_trackpoint_wait_minutes'
CONF_MAX_RUNTIME_HOURS='max_runtime_hours'
CONF_STALE_MINUTES='stale_minutes'
CONF_FINALIZATION_MINUTES='finalization_minutes'
CONF_RETAIN_ENDED_HOURS='retain_ended_hours'
CONF_DEFER_STARTUP_POLL_SECONDS='defer_startup_poll_seconds'
CONF_EXPOSE_DEBUG_ATTRIBUTES='expose_debug_attributes'
CONF_USER_AGENT='user_agent'
DEFAULT_LISTEN_TO_IMAP_EVENTS=True
DEFAULT_STRICT_USERS=False
DEFAULT_ACCEPT_FIRST_SEEN_USERS=False
DEFAULT_ALLOWED_USERS=[]
DEFAULT_ACTIVITY_FILTER='all'
DEFAULT_UPDATE_PROFILE='conservative'
DEFAULT_UPDATE_INTERVAL=timedelta(seconds=60)
DEFAULT_USE_GARMIN_TRACKPOINT_FREQUENCY=False
DEFAULT_INITIAL_TRACKPOINT_WAIT=timedelta(minutes=10)
DEFAULT_MAX_RUNTIME_HOURS=23
DEFAULT_STALE_MINUTES=15
DEFAULT_FINALIZATION_MINUTES=10
DEFAULT_RETAIN_ENDED_HOURS=24
DEFAULT_DEFER_STARTUP_POLL_SECONDS=60
DEFAULT_EXPOSE_DEBUG_ATTRIBUTES=False
DEFAULT_USER_AGENT='HomeAssistant-GarminLiveTrack/0.1.2'
ACTIVITY_VALUES=['all','running','walking','cycling','strength','swimming','kayak','rowing','other']
UPDATE_PROFILE_VALUES=['extended','conservative','balanced','adaptive','custom']
UPDATE_PROFILE_DEFAULT_INTERVALS={
    'extended': 600,
    'conservative': 60,
    'balanced': 30,
    'adaptive': 15,
    'custom': 60,
}
UPDATE_PROFILE_DEFAULT_USE_GARMIN_TRACKPOINT_FREQUENCY={
    'extended': False,
    'conservative': False,
    'balanced': False,
    'adaptive': True,
    'custom': False,
}
UPDATE_PROFILE_DEFAULT_INITIAL_WAIT_MINUTES={
    'extended': 20,
    'conservative': 10,
    'balanced': 10,
    'adaptive': 10,
    'custom': 10,
}
UPDATE_PROFILE_DEFAULT_STALE_MINUTES={
    'extended': 30,
    'conservative': 15,
    'balanced': 15,
    'adaptive': 15,
    'custom': 15,
}
STORAGE_KEY='garmin_livetrack.storage'
STORAGE_VERSION=1
RUNTIME_DATA='runtime_data'
