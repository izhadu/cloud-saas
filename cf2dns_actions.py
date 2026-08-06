#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Mail: tongdongdong@outlook.com

import sys, os, json, requests, time, traceback

from dns.qCloud import QcloudApiv3
from dns.aliyun import AliApi
from dns.huawei import HuaWeiApi

config = json.loads(os.environ.get("CONFIG", "{}"))
DOMAINS = json.loads(os.environ.get("DOMAINS", "{}"))
provider_data = json.loads(os.environ.get("PROVIDER", "[]"))

def get_optimization_ip(iptype):
    """获取动态 API 优选 IP (用于日常加速)"""
    try:
        headers = {'Content-Type': 'application/json'}
        data = {"key": config.get("key"), "type": iptype}
        provider = next((item for item in provider_data if item['id'] == config.get("data_server")), None)
        
        if not provider:
            print("CHANGE OPTIMIZATION IP ERROR: PROVIDER NOT FOUND")
            return None

        response = requests.post(provider['get_ip_url'], json=data, headers=headers, timeout=15)
        if response.status_code == 200:
            res_data = response.json()
            if res_data and "info" in res_data:
                for line_key in res_data["info"]:
                    res_data["info"][line_key] = sorted(
                        res_data["info"][line_key],
                        key=lambda x: (-float(x.get("speed", 0)), float(x.get("rtt_avg", 9999)))
                    )
            return res_data
        else:
            print(f"CHANGE OPTIMIZATION IP ERROR: HTTP {response.status_code}")
            return None
    except Exception as e:
        print(f"CHANGE OPTIMIZATION IP ERROR: {e}")
        return None

def get_static_ips_solid(url, top_n=3):
    """云端实在策略：绝对信任国内开源维护者的测速排名，严格按顺序截取榜单前 N 名"""
    try:
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            results = []
            for index, line in enumerate(response.text.splitlines()):
                line = line.strip()
                if line and not line.startswith("#"):
                    parts = line.split()
                    if parts:
                        ip = parts[0]
                        results.append({"ip": ip, "speed": 999.0, "rtt_avg": float(index)})
                        if len(results) >= top_n:
                            break
            return results
    except Exception as e:
        print(f"GET STATIC IPS ERROR: {e}")
    return []

def changeDNS(line, s_info, c_info, domain, sub_domain, cloud, iptype):
    if not c_info:
        return
        
    recordType = "AAAA" if iptype == 'v6' else "A"
    lines = {"CM": "移动", "CU": "联通", "CT": "电信", "AB": "境外", "DEF": "默认"}
    line_name = lines.get(line, "默认")
    
    # 华为云强制取 1 个以防格式错误，其余云取配置数量
    if config.get("dns_server") == 3:
        target_count = 1
    else:
        target_count = config.get("affect_num", 2)
    
    selected_ips = []
    for cf in c_info:
        if len(selected_ips) < target_count:
            selected_ips.append(cf["ip"])
        else:
            break

    # 解析当前云端已存在的 IP
    existing_ips = []
    for info in s_info:
        val = info.get("value")
        if isinstance(val, list):
            existing_ips.extend(val)
        elif isinstance(val, str) and "[" in val:
            try:
                parsed = json.loads(val.replace("'", '"'))
                if isinstance(parsed, list):
                    existing_ips.extend(parsed)
                else:
                    existing_ips.append(val)
            except:
                existing_ips.append(val)
        else:
            existing_ips.append(val)

    sub_tag = f"{sub_domain}." if sub_domain != "@" else ""
    full_domain = f"{sub_tag}{domain}"

    # 保护机制：如果当前 DNS 的 IP 已经是我们要优选的 IP，直接跳过，避免无效的删写
    if sorted(selected_ips) == sorted(existing_ips):
        print(f"SKIP UPDATE: ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----DOMAIN: {full_domain}----RECORDLINE: {line_name}----REASON: IPs unchanged.")
        return

    # ========== 核心修改：先彻底删除所有旧记录 ==========
    if s_info:
        for info in s_info:
            try:
                if hasattr(cloud, 'del_record'):
                    cloud.del_record(domain, info["recordId"])
                    print(f"DELETE OLD DNS SUCCESS: ----DOMAIN: {full_domain}----RECORDLINE: {line_name}----RECORDID: {info['recordId']}")
            except Exception as e:
                print(f"DELETE OLD DNS ERROR: ----DOMAIN: {full_domain}----MESSAGE: {e}")

    # ========== 再将优选出的 IP 循环作为全新记录写入 ==========
    for ip in selected_ips:
        try:
            ret = cloud.create_record(domain, sub_domain, ip, recordType, line_name, config["ttl"])
            if ret.get("code") == 0 or "id" in ret:
                print(f"CREATE NEW DNS SUCCESS: ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----DOMAIN: {full_domain}----RECORDLINE: {line_name}----VALUE: {ip}")
            else:
                print(f"CREATE NEW DNS ERROR: ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----RAW_RESPONSE: {ret}")
        except Exception as e:
            print(f"CREATE NEW DNS EXCEPTION: ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----MESSAGE:\n{e}")

def main(cloud, iptype):
    recordType = "AAAA" if iptype == 'v6' else "A"
    
    if not DOMAINS:
        return

    try:
        affect_num = config.get("affect_num", 2)

        # 1. 获取动态 API 优选 IP 
        cfips = get_optimization_ip(iptype)
        cf_cmips, cf_cuips, cf_ctips = [], [], []
        if cfips and cfips.get("code") == 200:
            cf_cmips = cfips["info"].get("CM", [])
            cf_cuips = cfips["info"].get("CU", [])
            cf_ctips = cfips["info"].get("CT", [])

        # 2. 严格按顺序截取静态高分库前 N 名
        static_icn_url = "https://raw.githubusercontent.com/yuanxiawan/cfipv4db/refs/heads/main/high_score_ips.txt"
        static_ips = get_static_ips_solid(static_icn_url, top_n=affect_num)
        if not static_ips:
            print(f"GET STATIC IPS FAILED: ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        for domain, sub_domains in DOMAINS.items():
            for sub_domain, lines in sub_domains.items():
                if sub_domain == "cf":
                    if not static_ips:
                        continue
                    current_cm, current_cu, current_ct = static_ips.copy(), static_ips.copy(), static_ips.copy()
                else:
                    current_cm, current_cu, current_ct = cf_cmips.copy(), cf_cuips.copy(), cf_ctips.copy()

                ret = cloud.get_record(domain, 100, sub_domain, recordType)
                if config["dns_server"] != 1 or ret.get("code") == 0:
                    cm_info, cu_info, ct_info, ab_info, def_info = [], [], [], [], []
                    
                    for record in ret.get("data", {}).get("records", []):
                        info = {"recordId": record["id"], "value": record["value"]}
                        if record["line"] == "移动":
                            cm_info.append(info)
                        elif record["line"] == "联通":
                            cu_info.append(info)
                        elif record["line"] == "电信":
                            ct_info.append(info)
                        elif record["line"] == "境外":
                            ab_info.append(info)
                        elif record["line"] == "默认":
                            def_info.append(info)
                            
                    for line in lines:
                        if line == "CM":
                            changeDNS("CM", cm_info, current_cm, domain, sub_domain, cloud, iptype)
                        elif line == "CU":
                            changeDNS("CU", cu_info, current_cu, domain, sub_domain, cloud, iptype)
                        elif line == "CT":
                            changeDNS("CT", ct_info, current_ct, domain, sub_domain, cloud, iptype)
                            
    except Exception as e:
        print(f"MAIN ERROR: ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----MESSAGE:\n{traceback.format_exc()}")

if __name__ == '__main__':
    cloud = None
    if config.get("dns_server") == 1:
        cloud = QcloudApiv3(config["secretid"], config["secretkey"])
    elif config.get("dns_server") == 2:
        cloud = AliApi(config["secretid"], config["secretkey"], config["region_ali"])
    elif config.get("dns_server") == 3:
        cloud = HuaWeiApi(config["secretid"], config["secretkey"], config["region_hw"])
        
    if cloud:
        if config.get("ipv4") == "on":
            main(cloud, "v4")
        if config.get("ipv6") == "on":
            main(cloud, "v6")
