import json
import os

from dailycheckin import CheckIn


def _disabled_platforms() -> set[str]:
    """env DAILYCHECKIN_DISABLE=LDOH,PKULAW 关闭指定平台 (大小写不敏感).

    设计: 只关不白名单, 默认全跑 (兼容上游行为). 白名单需求用 main.py --include.
    """
    raw = os.getenv("DAILYCHECKIN_DISABLE", "").strip()
    if not raw:
        return set()
    return {s.strip().upper() for s in raw.split(",") if s.strip()}


# 历史: ldoh/main.py 类名是 LdohCheckIn (upper 后是 LDOHCHECKIN), 但 config 写 "LDOH".
# Tencent Video: 类名 TencentVideo (upper TENCENTVIDEO), config 习惯写 "TENCENT_VIDEO".
# 这里用 alias 兼容历史命名, 同时保持 PEP8 类名风格.
PLATFORM_KEY_ALIASES = {
    "LDOHCHECKIN": "LDOH",
    "TENCENTVIDEO": "TENCENT_VIDEO",
}


def checkin_map():
    result = {}
    disabled = _disabled_platforms()
    for cls in CheckIn.__subclasses__():
        check_name = cls.__name__.upper()
        if not check_name or check_name in disabled:
            continue
        result[check_name] = (cls.name, cls)
    # 应用 key alias
    for old, new in PLATFORM_KEY_ALIASES.items():
        if old in result and new not in result:
            result[new] = result.pop(old)
    return result


checkin_map = checkin_map()

notice_map = {
    "BARK_URL": "",
    "COOLPUSHEMAIL": "",
    "COOLPUSHQQ": "",
    "COOLPUSHSKEY": "",
    "COOLPUSHWX": "",
    "DINGTALK_ACCESS_TOKEN": "",
    "DINGTALK_SECRET": "",
    "FSKEY": "",
    "PUSHPLUS_TOKEN": "",
    "PUSHPLUS_TOPIC": "",
    "QMSG_KEY": "",
    "QMSG_TYPE": "",
    "QYWX_AGENTID": "",
    "QYWX_CORPID": "",
    "QYWX_CORPSECRET": "",
    "QYWX_KEY": "",
    "QYWX_TOUSER": "",
    "QYWX_MEDIA_ID": "",
    "QYWX_ORIGIN": "",
    "SCKEY": "",
    "SENDKEY": "",
    "TG_API_HOST": "",
    "TG_BOT_TOKEN": "",
    "TG_PROXY": "",
    "TG_USER_ID": "",
    "MERGE_PUSH": "",
    "GOTIFY_URL": "",
    "GOTIFY_TOKEN": "",
    "GOTIFY_PRIORITY": "",
    "NTFY_URL": "",
    "NTFY_TOPIC": "",
    "NTFY_PRIORITY": "",
}


def env2list(key):
    try:
        value = json.loads(os.getenv(key, []).strip()) if os.getenv(key) else []
        if not isinstance(value, list):
            value = []
    except Exception as e:
        print(e)
        value = []
    return value


def env2str(key):
    try:
        value = os.getenv(key, "") if os.getenv(key) else ""
        if isinstance(value, str):
            value = value.strip()
        elif isinstance(value, bool):
            pass
        else:
            value = None
    except Exception as e:
        print(e)
        value = None
    return value


def get_checkin_info(data):
    result = {}
    if isinstance(data, dict):
        for one in checkin_map.keys():
            result[one.lower()] = data.get(one, [])
    else:
        for one in checkin_map.keys():
            result[one.lower()] = env2list(one)
    return result


def get_notice_info(data):
    result = {}
    if isinstance(data, dict):
        for one in notice_map.keys():
            result[one.lower()] = data.get(one, None)
    else:
        for one in notice_map.keys():
            result[one.lower()] = env2str(one)
    return result
