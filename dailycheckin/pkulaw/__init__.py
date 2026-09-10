"""pkulaw 北大法宝每日签到模块。

子模块:
  - main.py: 主签到流程 (含 OAuth refresh)
  - auth.py: Keycloak token 解析 + 续期 + 配置写回
  - login_helper.py: 浏览器一次登录, 捕获 token (CDP)
"""