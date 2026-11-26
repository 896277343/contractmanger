"""Windows OA-to-contract sync. OA credentials and cookies exist in memory only."""
import json
import re
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, ProxyHandler, Request, build_opener
from http.cookiejar import CookieJar

from contracts.oa_client import OAClient, describe_oa_page
from contracts.parser import parse_oa


SERVER = "https://contracts.68988008.xyz"
OA_ADDRESS = "http://192.168.1.80:8080"


class SameOriginRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if (parsed.scheme, parsed.netloc) != ("https", urlparse(SERVER).netloc):
            raise ValueError("合同系统返回了非预期跳转，已停止连接")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


class ContractSession:
    def __init__(self):
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()), SameOriginRedirect())
        self.csrf = ""

    def request(self, path, data=None, form=False):
        url = urljoin(SERVER + "/", path.lstrip("/"))
        headers = {"Accept": "application/json", "User-Agent": "ContractManagerDesktop/1.0"}
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
            headers["X-CSRFToken"] = self.csrf
            body = urlencode(data).encode("utf-8") if form else json.dumps(data, ensure_ascii=False).encode("utf-8")
        else:
            body = None
        try:
            with self.opener.open(Request(url, data=body, headers=headers), timeout=20) as response:
                raw = response.read(1024 * 1024 + 1)
        except HTTPError as exc:
            raw = exc.read(8192)
            try:
                detail = json.loads(raw)["error"]
            except (ValueError, KeyError, TypeError):
                detail = f"服务器返回 HTTP {exc.code}"
            raise ValueError(detail) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ValueError("无法连接合同系统，请检查网络和 HTTPS 证书") from exc
        if len(raw) > 1024 * 1024:
            raise ValueError("合同系统响应过大")
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise ValueError("合同系统没有返回接口数据，请确认服务器已更新") from exc

    def login(self, username, password):
        self.csrf = self.request("api/desktop/csrf/")["csrf_token"]
        result = self.request("api/desktop/login/", {"username": username, "password": password}, form=True)
        self.csrf = result["csrf_token"]
        return result["username"]

    def sync(self, data):
        fields = ("oa_number", "contract_number", "customer_contract_number", "oa_signer", "customer_name", "currency", "sha256", "materials", "outbound_items")
        return self.request("api/desktop/sync/", {key: data[key] for key in fields})


class SyncApp:
    def __init__(self, root):
        self.root = root
        self.root.title("合同管理 · OA 同步工具")
        self.root.geometry("650x650")
        self.root.minsize(580, 560)
        self.contract = None
        self.oa = None
        self.preview = None
        self.busy = False
        frame = ttk.Frame(root, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="合同管理 · OA 同步", font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")
        ttk.Label(frame, text="在公司内网读取 OA；只将合同号、物料和出库信息同步到合同系统。", wraplength=570).pack(anchor="w", pady=(5, 15))
        self.contract_user = self._field(frame, "合同系统用户名")
        self.contract_password = self._field(frame, "合同系统密码", secret=True)
        self.contract_button = ttk.Button(frame, text="登录合同系统", command=self.login_contract)
        self.contract_button.pack(anchor="w", pady=(4, 14))
        self.oa_user = self._field(frame, "OA 用户名")
        self.oa_password = self._field(frame, "OA 密码", secret=True)
        self.oa_button = ttk.Button(frame, text="登录 OA（本次运行只需一次）", command=self.login_oa)
        self.oa_button.pack(anchor="w", pady=(4, 14))
        self.run_id = self._field(frame, "OA 流水号")
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(3, 12))
        self.fetch_button = ttk.Button(buttons, text="获取并预览", command=self.fetch)
        self.fetch_button.pack(side="left")
        self.sync_button = ttk.Button(buttons, text="确认同步到 VPS", command=self.sync, state="disabled")
        self.sync_button.pack(side="left", padx=12)
        self.status = tk.StringVar(value="请先登录合同系统和 OA。")
        ttk.Label(frame, textvariable=self.status, wraplength=570).pack(anchor="w", pady=(0, 8))
        self.summary = tk.Text(frame, height=14, wrap="word", state="disabled", font=("Microsoft YaHei UI", 10))
        self.summary.pack(fill="both", expand=True)
        ttk.Label(frame, text="OA 密码和会话只保留在工具内存中；关闭窗口即清除。", foreground="#5b6472").pack(anchor="w", pady=(8, 0))

    def _field(self, parent, label, secret=False):
        ttk.Label(parent, text=label).pack(anchor="w")
        entry = ttk.Entry(parent, show="*" if secret else "")
        entry.pack(fill="x", pady=(2, 8))
        return entry

    def _work(self, status, action, complete):
        if self.busy:
            return
        self.busy = True
        self.status.set(status)
        for button in (self.contract_button, self.oa_button, self.fetch_button, self.sync_button):
            button.configure(state="disabled")

        def run():
            try:
                result = action()
            except Exception as exc:
                self.root.after(0, lambda: self._done_error(str(exc)))
            else:
                self.root.after(0, lambda: self._done_success(complete, result))

        threading.Thread(target=run, daemon=True).start()

    def _finish(self):
        self.busy = False
        for button in (self.contract_button, self.oa_button, self.fetch_button):
            button.configure(state="normal")
        self.sync_button.configure(state="normal" if self.preview and self.contract else "disabled")

    def _done_error(self, detail):
        self._finish()
        self.status.set(detail)
        messagebox.showerror("操作未完成", detail)

    def _done_success(self, complete, result):
        self._finish()
        complete(result)

    def _show(self, text):
        self.summary.configure(state="normal")
        self.summary.delete("1.0", "end")
        self.summary.insert("1.0", text)
        self.summary.configure(state="disabled")

    def login_contract(self):
        username, password = self.contract_user.get().strip(), self.contract_password.get()
        if not username or not password:
            messagebox.showerror("缺少信息", "请输入合同系统用户名和密码")
            return
        self.contract = None
        self.preview = None
        self._work("正在登录合同系统…", lambda: self._contract_login(username, password), self._contract_logged_in)

    def _contract_login(self, username, password):
        session = ContractSession()
        user = session.login(username, password)
        return session, user

    def _contract_logged_in(self, result):
        self.contract, user = result
        self.contract_password.delete(0, "end")
        self.status.set(f"合同系统已登录：{user}")

    def login_oa(self):
        username, password = self.oa_user.get().strip(), self.oa_password.get()
        if not username or not password:
            messagebox.showerror("缺少信息", "请输入 OA 用户名和密码")
            return
        self.oa = None
        self.preview = None
        self._work("正在登录 OA…", lambda: self._oa_login(username, password), self._oa_logged_in)

    def _oa_login(self, username, password):
        client = OAClient(OA_ADDRESS)
        client.login(username, password)
        return client

    def _oa_logged_in(self, client):
        self.oa = client
        self.oa_password.delete(0, "end")
        self.status.set("OA 登录请求已完成。输入流水号获取表单后会再次核验登录状态。")

    def fetch(self):
        run_id = self.run_id.get().strip()
        if not self.oa or not re.fullmatch(r"\d{1,12}", run_id):
            messagebox.showerror("缺少信息", "请先登录 OA 并输入有效流水号")
            return
        self.preview = None
        self.sync_button.configure(state="disabled")
        self._work("正在读取 OA 表单…", lambda: self._fetch_oa(run_id), self._fetched)

    def _fetch_oa(self, run_id):
        html = self.oa.fetch(run_id)
        try:
            data = parse_oa(html)
        except ValueError as exc:
            raise ValueError(f"OA 表单无法识别合同号：{describe_oa_page(html)}") from exc
        if data["oa_number"] and data["oa_number"] != run_id:
            raise ValueError("OA 表单流水号与输入值不一致，已停止同步")
        data["oa_number"] = run_id
        data["currency"] = data["items"][0]["currency"] if data["items"] else ""
        return data

    def _fetched(self, data):
        self.preview = data
        self.sync_button.configure(state="normal" if self.contract else "disabled")
        lines = [f"OA 流水号：{data['oa_number']}", f"合同号：{data['contract_number']}", f"签订单位：{data['customer_name']}", f"合同签订人：{data['oa_signer']}", f"供货物料：{len(data['materials'])} 条", f"已发货物料：{len(data['outbound_items'])} 条", "", "供货物料预览："]
        lines += [f"{item['position']}. {item['code']}  {item['name']}  {item['specification']}  × {item['quantity'] or '-'}" for item in data["materials"][:30]]
        if len(data["materials"]) > 30:
            lines.append(f"另有 {len(data['materials']) - 30} 条未在预览中展开")
        self._show("\n".join(lines))
        self.status.set("已获取 OA 数据。请核对合同号和物料，再确认同步。")

    def sync(self):
        if not self.contract or not self.preview:
            return
        data = self.preview
        if not messagebox.askyesno("确认同步", f"将 OA {data['oa_number']} 的数据同步到合同 {data['contract_number']}？\n服务器将按当前登录账号判断合同归属。"):
            return
        self._work("正在同步到 VPS…", lambda: self.contract.sync(data), self._synced)

    def _synced(self, result):
        self.preview = None
        self.sync_button.configure(state="disabled")
        self.status.set(result["message"])
        messagebox.showinfo("同步完成", result["message"])


if __name__ == "__main__":
    root = tk.Tk()
    SyncApp(root)
    root.mainloop()
