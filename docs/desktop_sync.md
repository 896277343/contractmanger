# OA 桌面同步工具

业务员在能访问 OA 内网的 Windows 电脑上运行 `ContractOA同步工具.exe`。工具先登录合同系统，再用 OA 账号建立本机会话；一次启动期间可连续读取多个 OA 流水号。每条数据先预览，再点击确认同步。

同步前，VPS 先运行现有更新脚本：

```bash
sudo bash /opt/contractmanger/scripts/update_server.sh
```

工具固定连接 `https://contracts.68988008.xyz`，本机 OA 地址为 `http://192.168.1.80:8080`。工具只提交解析出的合同号、币种、签订单位、物料和出库信息；不提交 OA 账号、密码、Cookie、整页 HTML 或合同金额。OA 密码及会话只保留在程序内存，关闭窗口即失效。合同系统登录密码也只保留在内存，登录成功后清空输入框。

业务员本人同步时，新合同归属本人。管理员或信息处理员代录新合同时，服务器按 OA“合同签订人”与业务员账号用户名或姓名精确匹配，预览中显示归属；未匹配或重名会停止同步。已有合同保留原归属。重复同步同一 OA 流水号不会重复添加相同物料或出库记录，Line items 不会改变，操作写入更新日志。所有同步请求需要合同系统会话与 CSRF 令牌。

从源码重新打包，在 Windows PowerShell 中运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build_desktop.ps1
```

生成文件位于 `dist/ContractOA同步工具.exe`。发送给业务员前可核对 SHA-256 摘要，避免发错版本。Windows 可能对未经代码签名的 EXE 显示来源提示；正式大范围分发可再配置代码签名。
