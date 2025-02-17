import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackContext
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim
import ssl
import requests
from requests.auth import HTTPBasicAuth
import asyncio
import urllib3
import json
import subprocess
import paramiko

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
ESXI_HOST = os.getenv('ESXI_HOST')
ESXI_USER = os.getenv('ESXI_USER')
ESXI_PASSWORD = os.getenv('ESXI_PASSWORD')
USER_PERMISSIONS = json.loads(os.getenv('USER_PERMISSIONS'))

def connect_to_esxi():
    context = ssl._create_unverified_context()
    si = SmartConnect(
        host=ESXI_HOST,
        user=ESXI_USER,
        pwd=ESXI_PASSWORD,
        sslContext=context
    )
    return si


def is_user_authorized(user_id, command):
    str_user_id = str(user_id)  
    if str_user_id in USER_PERMISSIONS and command in USER_PERMISSIONS[str_user_id]:
        return True
    return False

def permission_required(command):
    def decorator(func):
        async def wrapper(update: Update, context: CallbackContext, *args, **kwargs):
            user_id = update.effective_user.id  
            if not is_user_authorized(user_id, command):
                await update.message.reply_text("You do not have permission to execute this command.")
                return
            await func(update, context, *args, **kwargs)
        return wrapper
    return decorator

def delete_datastore_file(si, datastore_path, datastore_name):
    try:
        file_manager = si.content.fileManager
        delete_task = file_manager.DeleteDatastoreFile_Task(
            name=f"[{datastore_name}] {datastore_path}",
            datacenter=si.content.rootFolder.childEntity[0]  
        )


        while delete_task.info.state not in [vim.TaskInfo.State.success, vim.TaskInfo.State.error]:
            pass

        if delete_task.info.state == vim.TaskInfo.State.success:
            print(f"File {datastore_path} deleted successfully.")
        else:
            print(f"Failed to delete file {datastore_path}: {delete_task.info.error.msg}")
    except Exception as e:
        print(f"Error deleting file: {str(e)}")

@permission_required('list')
async def list_vms(update: Update, context: CallbackContext):
    if not update.message:
        return  
    si = connect_to_esxi()
    content = si.RetrieveContent()
    vms = content.viewManager.CreateContainerView(
        content.rootFolder, [vim.VirtualMachine], True).view
    vm_status_list = [f"{vm.name}: {'Running' if vm.runtime.powerState == 'poweredOn' else 'Stopped'}" for vm in vms]
    await update.message.reply_text("\n".join(vm_status_list))
    Disconnect(si)

@permission_required('start')
async def start_vm(update: Update, context: CallbackContext):
    if not update.message:
        return  
    vm_name = ' '.join(context.args)
    if not vm_name:
        await update.message.reply_text("ERROR: Please provide a VM name.")
        return

    si = connect_to_esxi()
    content = si.RetrieveContent()
    vm = next((vm for vm in content.viewManager.CreateContainerView(
        content.rootFolder, [vim.VirtualMachine], True).view if vm.name == vm_name), None)

    if vm:
        if vm.runtime.powerState == 'poweredOff':
            task = vm.PowerOn()
            await update.message.reply_text(f"Starting VM: {vm_name}")
        else:
            await update.message.reply_text(f"VM {vm_name} is already running.")
    else:
        await update.message.reply_text(f"VM {vm_name} not found.")
    Disconnect(si)

@permission_required('stop')
async def stop_vm(update: Update, context: CallbackContext):
    if not update.message:
        return  
    vm_name = ' '.join(context.args)
    if not vm_name:
        await update.message.reply_text("ERROR: Please provide a VM name.")
        return

    si = connect_to_esxi()
    content = si.RetrieveContent()
    vm = next((vm for vm in content.viewManager.CreateContainerView(
        content.rootFolder, [vim.VirtualMachine], True).view if vm.name == vm_name), None)

    if vm:
        if vm.runtime.powerState == 'poweredOn':
            task = vm.PowerOff()
            await update.message.reply_text(f"Stopping VM: {vm_name}")
        else:
            await update.message.reply_text(f"VM {vm_name} is already stopped.")
    else:
        await update.message.reply_text(f"VM {vm_name} not found.")
    Disconnect(si)

@permission_required('reset')
async def reset_vm(update: Update, context: CallbackContext):
    if not update.message:
        return  
    vm_name = ' '.join(context.args)
    if not vm_name:
        await update.message.reply_text("ERROR: Please provide a VM name.")
        return

    si = connect_to_esxi()
    content = si.RetrieveContent()
    vm = next((vm for vm in content.viewManager.CreateContainerView(
        content.rootFolder, [vim.VirtualMachine], True).view if vm.name == vm_name), None)

    if vm:
        if vm.runtime.powerState == 'poweredOn':
            task = vm.ResetVM_Task()
            await update.message.reply_text(f"Resetting VM: {vm_name}")
        else:
            await update.message.reply_text(f"VM {vm_name} is not running, so it cannot be reset.")
    else:
        await update.message.reply_text(f"VM {vm_name} not found.")
    Disconnect(si)

@permission_required('screenshot')
async def screenshot_vm(update: Update, context: CallbackContext):
    if not update.message:
        return  

    vm_name = ' '.join(context.args)
    if not vm_name:
        await update.message.reply_text("ERROR: Please provide a VM name.")
        return

    try:
        si = connect_to_esxi()
        content = si.RetrieveContent()
        vm = next((vm for vm in content.viewManager.CreateContainerView(
            content.rootFolder, [vim.VirtualMachine], True).view if vm.name == vm_name), None)

        if vm:
            if vm.runtime.powerState == 'poweredOn':
                task = vm.CreateScreenshot_Task()
                while task.info.state not in [vim.TaskInfo.State.success, vim.TaskInfo.State.error]:
                    pass

                if task.info.state == vim.TaskInfo.State.success:
                    screenshot_result = task.info.result
                    print(screenshot_result)

                    if screenshot_result.startswith("["):
                        datastore_path = screenshot_result.split('] ')[-1]
                        datastore_name = screenshot_result.split('[')[-1].split(']')[0]
                        download_url = f"https://{os.getenv('ESXI_HOST')}/folder/{datastore_path}?dcPath=ha-datacenter&dsName={datastore_name}"
                        download_url = download_url.replace(' ', '%20')
                        response = requests.get(download_url, auth=HTTPBasicAuth(os.getenv('ESXI_USER'), os.getenv('ESXI_PASSWORD')), verify=False)
                        if response.status_code == 200:
                            with open(f"{vm_name}_screenshot.png", "wb") as image_file:
                                image_file.write(response.content)

                            with open(f"{vm_name}_screenshot.png", "rb") as image_file:
                                await update.message.reply_photo(photo=image_file)

                            os.remove(f"{vm_name}_screenshot.png")
                            delete_datastore_file(si, datastore_path, datastore_name)
                        else:
                            await update.message.reply_text("Failed to download the screenshot.")
                    else:
                        await update.message.reply_text("Unexpected screenshot data format.")
            else:
                await update.message.reply_text(f"VM {vm_name} is not running.")

        else:
            await update.message.reply_text(f"VM {vm_name} not found.")
        Disconnect(si)

    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}")

@permission_required('clone')
async def clone_vm(update: Update, context: CallbackContext):
    if not update.message:
        return
    
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("ERROR: Please provide both source VM name and new VM name.")
        return
    
    source_vm_name, new_vm_name = args[0], args[1]
    datastore = "HDD"  # Adjust based on your ESXi configuration
    
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    try:
        ssh.connect(ESXI_HOST, username=ESXI_USER, password=ESXI_PASSWORD)

        commands = [
            f"mkdir /vmfs/volumes/{datastore}/{new_vm_name}",
            f"vmkfstools -i /vmfs/volumes/{datastore}/{source_vm_name}/{source_vm_name}.vmdk /vmfs/volumes/{datastore}/{new_vm_name}/{new_vm_name}.vmdk -d thin",
            f"cp /vmfs/volumes/{datastore}/{source_vm_name}/{source_vm_name}.vmx /vmfs/volumes/{datastore}/{new_vm_name}/{new_vm_name}.vmx",
            f"sed -i 's/displayName = \"{source_vm_name}\"/displayName = \"{new_vm_name}\"/g' /vmfs/volumes/{datastore}/{new_vm_name}/{new_vm_name}.vmx",
            f"vim-cmd solo/registervm /vmfs/volumes/{datastore}/{new_vm_name}/{new_vm_name}.vmx"
        ]
        
        for command in commands:
            stdin, stdout, stderr = ssh.exec_command(command)
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                await update.message.reply_text(f"ERROR: {stderr.read().decode().strip()}")
                ssh.close()
                return
        
        await update.message.reply_text(f"Thin clone of VM '{source_vm_name}' created as '{new_vm_name}' and registered in ESXi.")
    
    except Exception as e:
        await update.message.reply_text(f"ERROR: Failed to clone VM: {str(e)}")
    finally:
        ssh.close()

@permission_required('delete')
async def delete_vm(update: Update, context: CallbackContext):
    if not update.message:
        return
    
    args = context.args
    if len(args) < 1:
        await update.message.reply_text("ERROR: Please provide the VM name to delete.")
        return
    
    vm_name = args[0]
    si = connect_to_esxi()
    content = si.RetrieveContent()
    vm = next((vm for vm in content.viewManager.CreateContainerView(
        content.rootFolder, [vim.VirtualMachine], True).view if vm.name == vm_name), None)
    
    if not vm:
        await update.message.reply_text(f"ERROR: VM '{vm_name}' not found.")
        Disconnect(si)
        return
    
    try:
        # Power off VM if running
        if vm.runtime.powerState == vim.VirtualMachinePowerState.poweredOn:
            task = vm.PowerOff()
            while task.info.state not in [vim.TaskInfo.State.success, vim.TaskInfo.State.error]:
                pass
            
        # Unregister the VM
        vm.UnregisterVM()
        await update.message.reply_text(f"VM '{vm_name}' has been unregistered from ESXi.")
        
    except Exception as e:
        await update.message.reply_text(f"ERROR: Failed to delete VM: {str(e)}")
    finally:
        Disconnect(si)


async def get_user_id(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    await update.message.reply_text(f"Your Telegram user ID is: {user_id}")

async def help_command(update: Update, context: CallbackContext):
    help_text = (
        "Here are the available commands and their descriptions:\n"
        "/list - List all VMs with their status.\n"
        "/start <vm> - Start a specific VM by name.\n"
        "/stop <vm> - Stop a specific VM by name.\n"
        "/reset <vm> - Reset a specific VM by name.\n"
        "/screenshot <vm> - Take a screenshot of a specific VM.\n"
        "/clone <vm> <new_vm> - Clone specific VM by name.\n"
        "/myid - Get your Telegram user ID (for setup).\n"
        "/help - Show this help message."
    )
    await update.message.reply_text(help_text)

def main():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("list", list_vms))
    application.add_handler(CommandHandler("start", start_vm))
    application.add_handler(CommandHandler("stop", stop_vm))
    application.add_handler(CommandHandler("reset", reset_vm))
    application.add_handler(CommandHandler("screenshot", screenshot_vm))
    application.add_handler(CommandHandler("clone", clone_vm))
    application.add_handler(CommandHandler("delete", delete_vm))
    application.add_handler(CommandHandler("myid", get_user_id))
    application.add_handler(CommandHandler("help", help_command))

    application.run_polling()

if __name__ == '__main__':
    asyncio.run(main())