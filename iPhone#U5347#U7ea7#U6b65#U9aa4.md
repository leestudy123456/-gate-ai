# Professional 3.0 iPhone 一次性升级

1. 解压更新包。
2. 在 GitHub 仓库根目录覆盖上传更新包根目录中的文件。
3. 进入 `templates`，覆盖 `index.html`。
4. 进入 `static`，覆盖 `app.js` 和 `style.css`。
5. 提交到 `main` 分支。
6. Render 选择 `Manual Deploy → Clear build cache & deploy`；没有该项则选择 `Deploy latest commit`。
7. 打开 `/api/health`，确认 `version` 为 `3.0.0`。
8. Safari 若仍显示旧界面，使用无痕窗口打开，或清除该网站缓存。

成功标志：顶部显示 `PRO 3.0.0`，并有“总览、研究、排行、预测”四个可切换标签。
