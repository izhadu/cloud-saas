#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Mail: tongdongdong@outlook.com

import sys, os, json, requests, time, traceback

from dns.qCloud import QcloudApiv3
from dns.aliyun import AliApi
from dns.huawei import HuaWeiApi

# 读取环境变量
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
            # 【核心优化】：按下载速度(speed)降序、延迟(latency)升序排列
            if res_data and "info" in res_data:
                for line_key in res_data["info"]:
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
    if iptype == 'v6':
        recordType = "AAAA"
    else:
        recordType = "A"

    lines = {"CM": "移动", "CU": "联通", "CT": "电信", "AB": "境外", "DEF": "默认"}
    line_name = lines.get(line, "默认")

    try:
        create_num = config["affect_num"] - len(s_info)
        
        # 记录已存在的 IP，避免重复处理
        existing_ips = [info.get("value") for info in s_info]
        
        if create_num == 0:
            for info in s_info:
                if not c_info:
                    break
                # 【优化】：直接取列表第一项（速度最快的），摒弃随机抽取
                cf_ip = c_info.pop(0)["ip"]
                
                if cf_ip in existing_ips:
                    continue
                    
                ret = cloud.change_record(domain, info["recordId"], sub_domain, cf_ip, recordType, line_name, config["ttl"])
                if config["dns_server"] != 1 or ret["code"] == 0:
                    print(f"CHANGE DNS SUCCESS: ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----DOMAIN: {domain}----SUBDOMAIN: {sub_domain}----RECORDLINE: {line_name}----VALUE: {cf_ip}")
                else:
                    print(f"CHANGE DNS ERROR: ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----MESSAGE: {ret.get('message', 'Unknown Error')}")
                    
        elif create_num > 0:
            for _ in range(create_num):
                if not c_info:
                    break
                cf_ip = c_info.pop(0)["ip"]
                
                if cf_ip in existing_ips:
                    continue
                    
                ret = cloud.create_record(domain, sub_domain, cf_ip, recordType, line_name, config["ttl"])
                if config["dns_server"] != 1 or ret["code"] == 0:
                    print(f"CREATE DNS SUCCESS: ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----DOMAIN: {domain}----SUBDOMAIN: {sub_domain}----RECORDLINE: {line_name}----VALUE: {cf_ip}")
                else:
                    print(f"CREATE DNS ERROR: ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----MESSAGE: {ret.get('message', 'Unknown Error')}")
                    
        else:
            for info in s_info:
                if create_num == 0 or not c_info:
                    break
                cf_ip = c_info.pop(0)["ip"]
                
                if cf_ip in existing_ips:
                    create_num += 1
                    continue
                    
                ret = cloud.change_record(domain, info["recordId"], sub_domain, cf_ip, recordType, line_name, config["ttl"])
                if config["dns_server"] != 1 or ret["code"] == 0:
                    print(f"CHANGE DNS SUCCESS: ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----DOMAIN: {domain}----SUBDOMAIN: {sub_domain}----RECORDLINE: {line_name}----VALUE: {cf_ip}")
                else:
                    print(f"CHANGE DNS ERROR: ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----MESSAGE: {ret.get('message', 'Unknown Error')}")
                create_num += 1
                
    except Exception as e:
        # 【修复】：正确输出异常堆栈，避免 None 导致日志混乱
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
                # 境外和默认线路，如果没有单独数据，默认使用电信的高速数据补足
                temp_cf_abips = cf_ctips.copy()
                temp_cf_defips = cf_ctips.copy()
                
                if config["dns_server"] == 1:
                    ret = cloud.get_record(domain, 20, sub_domain, "CNAME")
                    if ret["code"] == 0:
                        for record in ret["data"]["records"]:
                            if record["line"] in ["移动", "联通", "电信"]:
                                retMsg = cloud.del_record(domain, record["id"])
                                if retMsg["code"] == 0:
                                    print(f"DELETE DNS SUCCESS: ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----DOMAIN: {domain}----SUBDOMAIN: {sub_domain}----RECORDLINE: {record['line']}")
                                else:
                                    print(f"DELETE DNS ERROR: ----Time: {time.strftime('%Y-%m-%d %H:%M:%S')}----MESSAGE: {retMsg['message']}")
                                    
                ret = cloud.get_record(domain, 100, sub_domain, recordType)
                if config["dns_server"] != 1 or ret["code"] == 0:
                    if config["dns_server"] == 1 and "Free" in ret.get("data", {}).get("domain", {}).get("grade", "") and config["affect_num"] > 2:
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
