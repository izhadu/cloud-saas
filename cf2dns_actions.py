#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Mail: tongdongdong@outlook.com

import sys, os, json, requests, time, traceback, socket, concurrent.futures

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

def tcp_ping(ip, port=443, timeout=2.0):
    """对目标 IP 的 443 端口进行 TCP 延迟测速"""
    try:
        start = time.time()
        socket.create_connection((ip, port), timeout=timeout).close()
        return ip, (time.time() - start) * 1000
    except Exception:
        return ip, float('inf')

def get_static_ips_and_test(url, top_n=2):
    """拉取静态 IP 库并发起实时测速，返回延迟最低的 top_n 个 IP"""
    try:
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            raw_ips = []
            for line in response.text.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    parts = line.split()
                    if parts:
                        raw_ips.append(parts[0])

            results = []
            # 使用多线程并发测速，极大缩短等待时间
            with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
                futures = {executor.submit(tcp_ping, ip): ip for ip in raw_ips}
                for future in concurrent.futures.as_completed(futures):
                    ip, latency = future.result()
                    if latency != float('inf'):
                        # 包装成与动态 API 相同的数据字典格式
                        results.append({"ip": ip, "speed": 999.0, "rtt_avg": latency})
                        
            # 按延迟升序排序，取前 top_n 个
            results = sorted(results, key=lambda x: x["rtt_avg"])
            return results[:top_n]
    except Exception as e:
        print(f"GET & TEST STATIC IPS ERROR: {e}")
    return []

def changeDNS(line, s_info, c_info, domain, sub_domain, cloud, iptype):
    if not c_info:
        return
        
    recordType = "AAAA" if iptype == 'v6' else "A"
    lines = {"CM": "移动", "CU": "联通", "CT": "电信", "AB": "境外", "DEF": "默认"}
    line_name = lines.get(line, "默认")
    
    # 视频专用域名取 2 个 IP，日常加速域名受限于华为云 API 规则取 1 个
    target_count = 2 if sub_domain == "cf" else (1 if config.get("dns_server") == 3 else config.get("affect_num", 2))
    
    selected_ips = []
    for cf in c_info:
        if len(selected_ips) < target_count:
            selected_ips.append(cf["ip"])
        else:
            break

    # 针对华为云的特殊传参处理：cf 域名传列表（多个），saas 域名传单值
    value_to_pass = selected_ips if sub_domain == "cf" or config.get("dns_server") != 3 else selected_ips[0]

    try:
        # ==========================================
        # 华为云专版逻辑 (dns_server == 3)
        # ==========================================
        if config.get("dns_server") == 3:
            if s_info:
                target_record_id = None
                for info in s_info:
                    val = str(info.get("value", ""))
                    # 检查新选出的 IP 是否已完全包含在当前记录中
                    if all(ip in val for ip in selected_ips):
                        target_record_id = info["recordId"]
                        break

                sub_tag = f"{sub_domain}." if sub_domain != "@" else ""
                full_domain = f"{sub_tag}{domain}"

                if target_record_id:
                    print(f"SKIP UPDATE (HUAWEI): ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----DOMAIN: {full_domain}----RECORDLINE: {line_name}----REASON: IPs {selected_ips} already exist.")
                    for info in s_info:
                        if info["recordId"] != target_record_id:
                            try:
                                if hasattr(cloud, 'del_record'):
                                    cloud.del_record(domain, info["recordId"])
                            except Exception:
                                pass
                else:
                    record_id = s_info[0]["recordId"]
                    ret = cloud.change_record(domain, record_id, sub_domain, value_to_pass, recordType, line_name, config["ttl"])
                    
                    if ret.get("code") == 0 or "id" in ret:
                        print(f"CHANGE DNS SUCCESS (HUAWEI): ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----DOMAIN: {full_domain}----RECORDLINE: {line_name}----VALUE: {selected_ips}")
                    else:
                        print(f"CHANGE DNS ERROR (HUAWEI): ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----RAW_RESPONSE: {ret}")
                    
                    if len(s_info) > 1:
                        for extra_info in s_info[1:]:
                            try:
                                if hasattr(cloud, 'del_record'):
                                    cloud.del_record(domain, extra_info["recordId"])
                            except Exception:
                                pass
            else:
                sub_tag = f"{sub_domain}." if sub_domain != "@" else ""
                full_domain = f"{sub_tag}{domain}"
                ret = cloud.create_record(domain, sub_domain, value_to_pass, recordType, line_name, config["ttl"])
                if ret.get("code") == 0 or "id" in ret:
                    print(f"CREATE DNS SUCCESS (HUAWEI): ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----DOMAIN: {full_domain}----RECORDLINE: {line_name}----VALUE: {selected_ips}")
                else:
                    print(f"CREATE DNS ERROR (HUAWEI): ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----RAW_RESPONSE: {ret}")
                    
        # ==========================================
        # 其他云厂商 (DNSPod / Aliyun)
        # ==========================================
        else:
            existing_ips = [info.get("value") for info in s_info]
            flat_existing = []
            for val in existing_ips:
                if isinstance(val, list):
                    flat_existing.extend(val)
                elif isinstance(val, str) and "[" in val:
                    try:
                        flat_existing.extend(json.loads(val.replace("'", '"')))
                    except:
                        flat_existing.append(val)
                else:
                    flat_existing.append(val)

            if sorted(selected_ips) == sorted(flat_existing):
                return

            if s_info:
                record_id = s_info[0]["recordId"]
                value_to_pass = selected_ips[0] if len(selected_ips) == 1 else selected_ips
                ret = cloud.change_record(domain, record_id, sub_domain, value_to_pass, recordType, line_name, config["ttl"])
                
                if ret.get("code") == 0 and len(s_info) > 1:
                    for extra_info in s_info[1:]:
                        if hasattr(cloud, 'del_record'):
                            cloud.del_record(domain, extra_info["recordId"])
            else:
                value_to_pass = selected_ips[0] if len(selected_ips) == 1 else selected_ips
                ret = cloud.create_record(domain, sub_domain, value_to_pass, recordType, line_name, config["ttl"])
                
    except Exception as e:
        print(f"CHANGE DNS ERROR: ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----MESSAGE:\n{traceback.format_exc()}")

def main(cloud, iptype):
    recordType = "AAAA" if iptype == 'v6' else "A"
    
    if not DOMAINS:
        return

    try:
        # 1. 获取动态 API 优选 IP (用于 @ / saas.zhadu.com)
        cfips = get_optimization_ip(iptype)
        cf_cmips, cf_cuips, cf_ctips = [], [], []
        if cfips and cfips.get("code") == 200:
            cf_cmips = cfips["info"].get("CM", [])
            cf_cuips = cfips["info"].get("CU", [])
            cf_ctips = cfips["info"].get("CT", [])

        # 2. 实时测速并获取静态高分库 top 2 (用于 cf / cf.saas.zhadu.com)
        static_icn_url = "https://raw.githubusercontent.com/yuanxiawan/cfipv4db/refs/heads/main/high_score_ips.txt"
        static_ips = get_static_ips_and_test(static_icn_url, top_n=2)
        if not static_ips:
            print(f"GET STATIC IPS FAILED: ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        
        for domain, sub_domains in DOMAINS.items():
            for sub_domain, lines in sub_domains.items():
                # 区分数据源
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
