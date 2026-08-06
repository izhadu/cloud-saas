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
                    # 按速度降序，延迟升序，确保提取的第一个绝对是带宽之王
                    res_data["info"][line_key] = sorted(
                        res_data["info"][line_key],
                        key=lambda x: (-float(x.get("speed", 0)), float(x.get("latency", 9999)))
                    )
            return res_data
        else:
            print(f"CHANGE OPTIMIZATION IP ERROR: HTTP {response.status_code}")
            return None
    except Exception as e:
        print(f"CHANGE OPTIMIZATION IP ERROR: {e}")
        return None

def changeDNS(line, s_info, c_info, domain, sub_domain, cloud, iptype):
    if not c_info:
        return
        
    recordType = "AAAA" if iptype == 'v6' else "A"
    lines = {"CM": "移动", "CU": "联通", "CT": "电信", "AB": "境外", "DEF": "默认"}
    line_name = lines.get(line, "默认")
    
    # 华为云强制只用 1 个极速 IP
    target_count = 1 if config.get("dns_server") == 3 else config.get("affect_num", 2)
    
    selected_ips = []
    for cf in c_info:
        if len(selected_ips) < target_count:
            selected_ips.append(cf["ip"])
        else:
            break

    best_ip = selected_ips[0]

    try:
        # ==========================================
        # 华为云专版逻辑 (dns_server == 3)
        # ==========================================
        if config.get("dns_server") == 3:
            if s_info:
                # 核心防撞车逻辑：检查 best_ip 是否已经存在于现有的任意一条记录中
                target_record_id = None
                for info in s_info:
                    val = str(info.get("value", ""))
                    if best_ip in val:
                        target_record_id = info["recordId"]
                        break

                if target_record_id:
                    print(f"SKIP UPDATE (HUAWEI): ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----DOMAIN: {domain}----SUBDOMAIN: {sub_domain}----RECORDLINE: {line_name}----REASON: Best IP {best_ip} already exists.")
                    # 保留这条正确的记录，静默清理同线路下的其他冗余垃圾记录
                    for info in s_info:
                        if info["recordId"] != target_record_id:
                            try:
                                if hasattr(cloud, 'del_record'):
                                    cloud.del_record(domain, info["recordId"])
                                    print(f"CLEANUP SURPLUS DNS: ----DOMAIN: {domain}----RECORDID: {info['recordId']}")
                            except Exception:
                                pass
                else:
                    # best_ip 完全不存在，安全地拿第一条记录修改
                    record_id = s_info[0]["recordId"]
                    ret = cloud.change_record(domain, record_id, sub_domain, best_ip, recordType, line_name, config["ttl"])
                    
                    if ret.get("code") == 0:
                        print(f"CHANGE DNS SUCCESS (HUAWEI): ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----DOMAIN: {domain}----SUBDOMAIN: {sub_domain}----RECORDLINE: {line_name}----VALUE: {best_ip}")
                    else:
                        print(f"CHANGE DNS ERROR (HUAWEI): ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----RAW_RESPONSE: {ret}")
                    
                    # 修改完第一条后，清理剩下的旧记录
                    if len(s_info) > 1:
                        for extra_info in s_info[1:]:
                            try:
                                if hasattr(cloud, 'del_record'):
                                    cloud.del_record(domain, extra_info["recordId"])
                                    print(f"CLEANUP SURPLUS DNS: ----DOMAIN: {domain}----RECORDID: {extra_info['recordId']}")
                            except Exception:
                                pass
            else:
                # 没有任何旧记录，直接新建
                ret = cloud.create_record(domain, sub_domain, best_ip, recordType, line_name, config["ttl"])
                if ret.get("code") == 0:
                    print(f"CREATE DNS SUCCESS (HUAWEI): ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----DOMAIN: {domain}----SUBDOMAIN: {sub_domain}----RECORDLINE: {line_name}----VALUE: {best_ip}")
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
                print(f"SKIP UPDATE: ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----DOMAIN: {domain}----SUBDOMAIN: {sub_domain}----RECORDLINE: {line_name}----REASON: IPs unchanged.")
                return

            if s_info:
                record_id = s_info[0]["recordId"]
                value_to_pass = selected_ips[0] if len(selected_ips) == 1 else selected_ips
                ret = cloud.change_record(domain, record_id, sub_domain, value_to_pass, recordType, line_name, config["ttl"])
                
                if ret.get("code") == 0 and len(s_info) > 1:
                    for extra_info in s_info[1:]:
                        if hasattr(cloud, 'del_record'):
                            cloud.del_record(domain, extra_info["recordId"])
                            
                if config["dns_server"] != 1 or ret.get("code") == 0:
                    print(f"CHANGE DNS SUCCESS: ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----DOMAIN: {domain}----SUBDOMAIN: {sub_domain}----RECORDLINE: {line_name}----VALUE: {selected_ips}")
                else:
                    print(f"CHANGE DNS ERROR: ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----RAW_RESPONSE: {ret}")
            else:
                value_to_pass = selected_ips[0] if len(selected_ips) == 1 else selected_ips
                ret = cloud.create_record(domain, sub_domain, value_to_pass, recordType, line_name, config["ttl"])
                
                if config["dns_server"] != 1 or ret.get("code") == 0:
                    print(f"CREATE DNS SUCCESS: ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----DOMAIN: {domain}----SUBDOMAIN: {sub_domain}----RECORDLINE: {line_name}----VALUE: {selected_ips}")
                else:
                    print(f"CREATE DNS ERROR: ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----RAW_RESPONSE: {ret}")
                
    except Exception as e:
        print(f"CHANGE DNS ERROR: ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----MESSAGE:\n{traceback.format_exc()}")


def main(cloud, iptype):
    recordType = "AAAA" if iptype == 'v6' else "A"
    
    if not DOMAINS:
        return

    try:
        cfips = get_optimization_ip(iptype)
        if not cfips or cfips.get("code") != 200:
            print(f"GET CLOUDFLARE IP ERROR: ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
            return
            
        cf_cmips = cfips["info"].get("CM", [])
        cf_cuips = cfips["info"].get("CU", [])
        cf_ctips = cfips["info"].get("CT", [])
        
        for domain, sub_domains in DOMAINS.items():
            for sub_domain, lines in sub_domains.items():
                temp_cf_cmips = cf_cmips.copy()
                temp_cf_cuips = cf_cuips.copy()
                temp_cf_ctips = cf_ctips.copy()
                temp_cf_abips = cf_ctips.copy()
                temp_cf_defips = cf_ctips.copy()
                
                if config["dns_server"] == 1:
                    ret = cloud.get_record(domain, 20, sub_domain, "CNAME")
                    if ret.get("code") == 0:
                        for record in ret["data"]["records"]:
                            if record["line"] in ["移动", "联通", "电信"]:
                                retMsg = cloud.del_record(domain, record["id"])
                                if retMsg.get("code") == 0:
                                    print(f"DELETE DNS SUCCESS: ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----DOMAIN: {domain}----SUBDOMAIN: {sub_domain}----RECORDLINE: {record['line']}")
                                else:
                                    print(f"DELETE DNS ERROR: ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----MESSAGE: {retMsg.get('message')}")
                                    
                ret = cloud.get_record(domain, 100, sub_domain, recordType)
                if config["dns_server"] != 1 or ret.get("code") == 0:
                    if config["dns_server"] == 1 and "Free" in ret.get("data", {}).get("domain", {}).get("grade", "") and config.get("affect_num", 2) > 2:
                        config["affect_num"] = 2
                        
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
                            changeDNS("CM", cm_info, temp_cf_cmips, domain, sub_domain, cloud, iptype)
                        elif line == "CU":
                            changeDNS("CU", cu_info, temp_cf_cuips, domain, sub_domain, cloud, iptype)
                        elif line == "CT":
                            changeDNS("CT", ct_info, temp_cf_ctips, domain, sub_domain, cloud, iptype)
                        elif line == "AB":
                            changeDNS("AB", ab_info, temp_cf_abips, domain, sub_domain, cloud, iptype)
                        elif line == "DEF":
                            changeDNS("DEF", def_info, temp_cf_defips, domain, sub_domain, cloud, iptype)
                            
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
