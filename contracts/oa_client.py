"""Fetch one authenticated OA print page without persisting OA credentials."""
import http.cookiejar
import re
import secrets
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener

from bs4 import BeautifulSoup


LOGIN_KEY = re.compile(r'var\s+(modulus|exponent)\s*=\s*"([0-9A-Fa-f]+)"')
MAX_PAGE_BYTES = 5 * 1024 * 1024


def _get(opener, url, data=None, referer=None):
    headers = {"User-Agent": "Mozilla/5.0 (compatible; ContractManager/1.0)"}
    if referer:
        headers["Referer"] = referer
    with opener.open(Request(url, data=data, headers=headers), timeout=8) as response:
        content = response.read(MAX_PAGE_BYTES + 1)
    if len(content) > MAX_PAGE_BYTES:
        raise ValueError("OA 页面超过 5 MB，无法在线导入")
    return content


def _encrypt_password(password, page):
    text = page.decode("utf-8", errors="replace")
    keys = dict(LOGIN_KEY.findall(text))
    if not {"modulus", "exponent"} <= keys.keys():
        raise ValueError("OA 登录页缺少加密公钥，登录方式可能已变化")
    modulus = int(keys["modulus"], 16)
    exponent = int(keys["exponent"], 16)
    size = (modulus.bit_length() + 7) // 8
    message = password.encode("utf-8")
    if size < 64 or len(message) > size - 11:
        raise ValueError("OA 密码过长或登录公钥无效")
    padding = bytes(secrets.randbelow(255) + 1 for _ in range(size - len(message) - 3))
    block = b"\x00\x02" + padding + b"\x00" + message
    encrypted = pow(int.from_bytes(block, "big"), exponent, modulus)
    return f"{encrypted:0{size * 2}x}"


def _visible_text(page):
    soup = BeautifulSoup(page.decode("utf-8", errors="replace"), "html.parser")
    for node in soup(["script", "style"]):
        node.decompose()
    return soup.get_text(" ", strip=True)


def describe_oa_page(page):
    soup = BeautifulSoup(page.decode("utf-8", errors="replace"), "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else "无标题"
    titled_inputs = soup.find_all("input", attrs={"title": True})
    run_match = re.search(r'var\s+g_run_id\s*=\s*["\'](\d+)', page.decode("utf-8", errors="replace"))
    visible = _visible_text(page)
    excerpt = re.sub(r"\s+", " ", visible)[:180]
    return f"页面标题“{title[:40]}”、带字段名的输入框 {len(titled_inputs)} 个、页面流水号 {run_match.group(1) if run_match else '未找到'}、页面大小 {len(page)} 字节、页面提示“{excerpt or '无可见文字'}”"


def _form_frame_url(page, wrapper_url, run_id):
    soup = BeautifulSoup(page.decode("utf-8", errors="replace"), "html.parser")
    frame = soup.find("iframe", id="print_frm")
    if not frame or not frame.get("src"):
        raise ValueError("OA 打印页未包含原始表单框架，请确认流水号及访问权限")
    frame_url = urljoin(wrapper_url, frame["src"])
    parsed = urlparse(frame_url)
    wrapper = urlparse(wrapper_url)
    if (parsed.scheme, parsed.netloc) != (wrapper.scheme, wrapper.netloc) or parsed.path != "/general/approve_center/list/print.php":
        raise ValueError("OA 原始表单地址不符合预期")
    if parse_qs(parsed.query).get("RUN_ID") != [run_id]:
        raise ValueError("OA 原始表单流水号与输入不一致")
    return frame_url


class OAClient:
    def __init__(self, base_url=None):
        self.base = (base_url or "http://192.168.1.80:8080").rstrip("/")
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.logged_in = False

    def login(self, username, password):
        if not username or not password:
            raise ValueError("请输入 OA 用户名和密码")
        base = self.base
        opener = self.opener
        login_page = _get(opener, base + "/")
        encrypted = _encrypt_password(password, login_page)
        body = urlencode({"UNAME": username, "PASSWORD": encrypted, "encode_type": "1"}).encode("utf-8")
        self.login_result = _get(opener, base + "/logincheck.php", body, referer=base + "/")
        self.logged_in = True

    def fetch(self, run_id):
        if not re.fullmatch(r"[0-9]{1,12}", run_id):
            raise ValueError("请输入有效的 OA 流水号")
        if not self.logged_in:
            raise ValueError("请先登录 OA")
        base = self.base
        opener = self.opener
        query = urlencode({"actionType": "view", "MENU_FLAG": "", "RUN_ID": run_id, "PRCS_KEY_ID": "", "FLOW_ID": "199"})
        wrapper_url = base + "/general/approve_center/list/print/index.php?" + query
        print_page = _get(opener, wrapper_url)
        if "用户未登录" in _visible_text(print_page)[:150]:
            detail = _visible_text(self.login_result)[:120]
            raise ValueError("OA 登录未成功，打印页仍显示“用户未登录”" + (f"；OA 提示：{detail}" if detail else "；请检查账号、密码或验证码"))
        if BeautifulSoup(print_page, "html.parser").find("iframe", id="print_frm"):
            print_page = _get(opener, _form_frame_url(print_page, wrapper_url, run_id), referer=wrapper_url)
            if "用户未登录" in _visible_text(print_page)[:150]:
                raise ValueError("OA 原始表单显示“用户未登录”，请检查账号或重新登录")
        return print_page


def fetch_oa_html(username, password, run_id, base_url=None):
    client = OAClient(base_url)
    try:
        client.login(username, password)
        return client.fetch(run_id)
    except (TimeoutError, HTTPError, URLError, OSError) as exc:
        raise ValueError("服务器无法连接 OA 内网地址，或 OA 登录失败；请检查服务器到 OA 的网络及账号") from exc
