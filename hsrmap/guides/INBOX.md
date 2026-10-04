# 真实网页 Inbox

把浏览器「另存为」的浮脂攻略 HTML 放到：

```text
data/guides/inbox/
```

然后：

```bash
python -m hsrmap guides ingest data/guides/inbox/xxx.html --url "原始URL" --provider fake
```

有 `DEEPSEEK_API_KEY` 时，单页 canary：

```bash
python -m hsrmap guides extract --page <id> --provider deepseek
```

不要把第三方全文提交进 git。`data/guides/` 已忽略。
