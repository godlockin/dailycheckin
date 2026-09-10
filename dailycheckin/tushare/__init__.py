"""tushare.pro 每日签到 + 猜涨跌 (CDP 浏览器自动化)

参照 freqtrade-learning/aastocks/portfolio/tushare_tasks.py 的协议,
通过 Node CDP sidecar (cdp/bridge.mjs) 驱动常驻 Chrome 完成:

  1. 检查登录态 (页面文本含用户名)
  2. 未登录 -> navigate to /#/login, 填手机号+密码, 提交
  3. navigate to /#/user/privilege (签到页)
  4. eval JS 拿任务列表, 找 DAILY_SIGN 任务; 未完成则点 sign 按钮
  5. navigate to 猜涨跌页; period.status==1 且未投 -> 投 1 或 2 (random)

账号从 config.json 取, 没填则用环境变量 TUSHARE_USERNAME / TUSHARE_PASSWORD
(参考 freqtrade-learning 的 ~/.zsh/env.zsh)。
"""