import json
from pathlib import Path


COUNTRIES = json.loads((Path(__file__).parent / "static" / "contracts" / "geography.json").read_text(encoding="utf-8"))
CONTINENTS = {"AF": "非洲", "AN": "南极洲", "AS": "亚洲", "EU": "欧洲", "NA": "北美洲", "OC": "大洋洲", "SA": "南美洲"}
CHINA_REGIONS = {"安徽", "澳门", "北京", "福建", "甘肃", "广东", "广西", "贵州", "海南", "河北", "河南", "黑龙江", "湖北", "湖南", "吉林", "江苏", "江西", "辽宁", "内蒙古", "宁夏", "青海", "山东", "山西", "陕西", "上海", "四川", "天津", "西藏", "香港", "新疆", "云南", "浙江", "重庆", "台湾"}
ALIASES = {"美国": "US", "英国": "GB", "俄罗斯": "RU", "阿联酋": "AE", "韩国": "KR", "朝鲜": "KP", "香港": "HK", "澳门": "MO", "台湾": "TW"}


def find_country(value):
    value = (value or "").strip()
    if not value:
        return None
    if value in ALIASES:
        return ALIASES[value]
    if value in CHINA_REGIONS:
        return "CN"
    if value.upper() in COUNTRIES:
        return value.upper()
    matches = [code for code, row in COUNTRIES.items() if value.casefold() in (row["name"].casefold(), row["zh"].casefold(), row["pinyin"].casefold())]
    return matches[0] if len(matches) == 1 else None


def continent_for(value):
    code = find_country(value)
    return CONTINENTS.get(COUNTRIES[code]["continent"], "") if code else ""
