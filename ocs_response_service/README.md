# OCS Responses AI 题库服务

这是一个可单独部署的 HTTP 题库，接收 OCS `AnswererWrapper` 的 `title`、`options`、`type`，调用 OpenAI Responses 兼容接口并返回 OCS 可直接匹配的答案。

它会：

- 将题干、材料和各选项中的图片分开标记为“题干/材料图片”“选项 A 图片”等，再按 `input_text + input_image` 的顺序提交；
- 先在服务器下载 OCS 传来的图片 URL，转为 data URL 后交给 Responses，避免让模型自己访问超星地址；
- 识别 `___`、`＿＿`、`[BLANK_n]` 和 `[UNDERLINE]...[/UNDERLINE]`；
- 为单选/多选优先返回 OCS 原生支持的选项字母，为多空题用 `#` 分隔；
- 题目声明有图片但一张都无法读取时默认拒绝猜答案。

## Docker 部署

```bash
cd ocs_response_service
cp .env.example .env
# 编辑 .env
docker compose up -d --build
curl http://127.0.0.1:8000/health
```

如果前面有 Nginx，图片会以 Base64 放进 JSON，请把请求体上限设大一些，例如：

```nginx
client_max_body_size 32m;
proxy_read_timeout 240s;
```

公网部署务必设置 `SERVICE_ACCESS_TOKEN`，并使用 HTTPS。服务本身不保存题目，也把 Responses 请求的 `store` 设为 `false`。

## OCS 配置

先复制 [ocs-answerer-wrapper.json.example](./ocs-answerer-wrapper.json.example) 到 OCS 的“题库配置”，替换服务域名和 `X-Access-Token`。这个配置不要求修改 OCS，图片由服务器根据 URL 下载。

OCS 全域名版的 `GM_xmlhttpRequest` 在 `Content-Type: application/json` 时会自动把 `data` 序列化为 JSON，因此接口可以直接接收这三个字段。

建议把 OCS 的题库请求超时调到 180 到 240 秒；模型推理时间可能超过 OCS 默认的 60 秒。

## 受保护图片的可选浏览器取图

部分超星图片只能在已登录的浏览器中读取。如果标准配置返回“没有拿到任何可读图片”，给 OCS 源码应用：

```bash
git apply /path/to/ocs_response_service/ocs-patch/0001-await-data-handlers-and-preserve-underlines.patch
```

重新构建全域名版后，改用 [ocs-answerer-wrapper-browser-images.json.example](./ocs-answerer-wrapper-browser-images.json.example)。它在浏览器登录态中用 `GM_xmlhttpRequest` 读取图片，转成 data URL 后再交给本服务。补丁只把现有 `data.handler` 的返回值改为可 `await`，并保留下划线语义，不绑定具体题库地址。

## 下划线的边界

未修改的 OCS 使用 `.innerText`，只能传来真实的下划线字符，CSS 的 `<u>`、`text-decoration` 和底边框样式会在请求前丢失。服务端无法恢复已经丢掉的 DOM 样式。上面的可选 OCS 补丁会在提取文本前把这些样式写成 `[UNDERLINE]` 或 `[BLANK_n]` 标记。

## 直接运行

```bash
python -m pip install -r ocs_response_service/requirements.txt
uvicorn ocs_response_service.app:app --host 0.0.0.0 --port 8000
```

主要环境变量见 [.env.example](./.env.example)。`OPENAI_BASE_URL` 可以填官方地址，也可以填兼容 `/v1/responses` 的站点根地址；一次请求只使用这里选中的一个站点、一个模型和一个思考强度。
