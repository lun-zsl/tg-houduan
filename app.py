import os
import sys
import asyncio
from flask import Flask, render_template_string, request, jsonify
from flask_cors import CORS
from telethon import TelegramClient
from telethon.tl.functions.channels import InviteToChannelRequest
from telethon.errors import (
    UserPrivacyRestrictedError, 
    UserAlreadyParticipantError, 
    FloodWaitError
)

app = Flask(__name__)
CORS(app)  # 允许前端跨域访问

# 全局状态管理
running_tasks = {}
system_logs = "🌟 [系统通知] 后端服务已成功启动，等待前端指令...\n"

def append_log(text):
    """向全局日志面板追加一条新纪录"""
    global system_logs
    system_logs += f"{text}\n"
    print(text)

# --- 核心拉人业务逻辑（异步执行） ---
async def start_telegram_job(phone, api_id, api_hash, target_group, source_groups, max_count):
    append_log(f"🚀 [任务开始] 正在初始化账号: {phone}...")
    
    # 1. 创建并连接 Telegram 客户端
    # session 文件会保存在本地，下次免验证登录
    client = TelegramClient(f'session_{phone}', int(api_id), api_hash)
    
    try:
        await client.connect()
        
        # 2. 检查登录状态
        if not await client.is_user_authorized():
            append_log(f"⚠️ [验证提示] 账号 {phone} 尚未登录过本系统。")
            append_log(f"👉 请返回 Codespaces 终端查看，并按照提示输入你的 Telegram 验证码！")
            
            # 发送验证码请求，并等待终端输入
            await client.send_code_request(phone)
            # 在独立网页运行时，由于环境隔离，建议先通过 Codespaces 终端完成首次手动验证
            code = input(f"请输入手机号 {phone} 收到的 Telegram 验证码 (仅限首次验证): ")
            await client.sign_in(phone, code)
            
        append_log(f"✅ [登录成功] 账号 {phone} 已在线，开始处理群组任务...")

        # 3. 目标群和来源群解析
        try:
            target_entity = await client.get_entity(target_group)
            append_log(f"🎯 [目标锁定] 已成功定位目的地群组: {target_entity.title}")
        except Exception as e:
            append_log(f"❌ [错误] 无法找到目的地群组，请检查链接是否正确: {str(e)}")
            return

        source_list = [g.strip() for g in source_groups.split('\n') if g.strip()]
        append_log(f"📦 [群组分析] 侦测到 {len(source_list)} 个待采集的来源群组。")

        # 4. 循环采集并拉人
        invited_count = 0
        for s_group in source_list:
            if running_tasks.get(phone) != "running":
                break
                
            append_log(f"🔍 [开始采集] 正在读取群组数据: {s_group} ...")
            try:
                source_entity = await client.get_entity(s_group)
                
                # 遍历采集源群组的成员
                async for user in client.iter_participants(source_entity):
                    if running_tasks.get(phone) != "running" or invited_count >= int(max_count):
                        break
                        
                    # 过滤掉机器人和已经注销的账号
                    if user.bot or user.deleted:
                        continue
                        
                    # 尝试将用户拉入新群
                    try:
                        append_log(f"➕ [尝试拉取] 正在尝试邀请用户 @{user.username or user.id} ...")
                        await client(InviteToChannelRequest(target_entity, [user]))
                        invited_count += 1
                        append_log(f"🎉 [成功] 累计成功拉入: {invited_count} / {max_count} 人。")
                        
                        # 适当休眠，防止动作太快被 Telegram 官方风控封号
                        await asyncio.sleep(5)
                        
                    except UserPrivacyRestrictedError:
                        append_log(f"🛡️ [跳过] 用户设置了隐私保护，无法将其加入其他群组。")
                    except UserAlreadyParticipantError:
                        append_log(f"🤝 [跳过] 用户已经在目的地群组中。")
                    except FloodWaitError as e:
                        append_log(f"⏳ [限速警告] 触发 Telegram 频率限制，需要等待 {e.seconds} 秒...")
                        await asyncio.sleep(e.seconds)
                    except Exception as e:
                        append_log(f"⚙️ [常规跳过] 无法邀请此用户: {str(e)}")
                        await asyncio.sleep(2)
                        
            except Exception as e:
                append_log(f"❌ [错误] 采集源群组 {s_group} 失败: {str(e)}")

        append_log(f"🏁 [任务结束] 账号 {phone} 的任务执行完毕，共成功拉入 {invited_count} 人。")

    except Exception as e:
        append_log(f"💥 [系统异常] 发生未可知错误: {str(e)}")
    finally:
        running_tasks[phone] = "stopped"
        await client.disconnect()

# --- 后端 Web 接口（供前端网页调用） ---

@app.route('/get_logs', methods=['GET'])
def get_logs():
    """实时返回最新的控制台运行日志"""
    global system_logs
    return jsonify({"logs": system_logs})

@app.route('/start', methods=['POST'])
def start_task():
    """接收前端发送的数据，并在后台启动拉人脚本"""
    data = request.json
    phone = data.get('phone')
    api_id = data.get('api_id')
    api_hash = data.get('api_hash')
    target_group = data.get('target_group')
    count = data.get('count', 100)
    source_groups = data.get('source_groups')

    if not all([phone, api_id, api_hash, target_group]):
        return jsonify({"status": "缺少关键配置参数，请核对后再试"}), 400

    if running_tasks.get(phone) == "running":
        return jsonify({"status": "该账号正在执行拉人任务中，请勿重复启动"})

    # 标记状态并启动后台异步线程
    running_tasks[phone] = "running"
    
    # 启动异步后台任务（不阻塞网页接口）
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    import threading
    t = threading.Thread(target=lambda: loop.run_until_complete(
        start_telegram_job(phone, api_id, api_hash, target_group, source_groups, count)
    ))
    t.start()

    return jsonify({"status": f"任务已安全提交到后台！账号 {phone} 正在启动。"})

@app.route('/stop', methods=['POST'])
def stop_task():
    """接收前端强制停止的请求"""
    for phone in running_tasks:
        if running_tasks[phone] == "running":
            running_tasks[phone] = "stopped"
    append_log("🛑 [系统管理] 收到用户指令：已向所有正在跑的账号发送停止信号！")
    return jsonify({"status": "停止信号已发出，系统正在刹车"})

if __name__ == '__main__':
    # 绑定全局端口并启动，供 Codespaces 映射网络
    app.run(host='0.0.0.0', port=5000, debug=True)
