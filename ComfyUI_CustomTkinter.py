import os
import json
import sys
import time
import subprocess
import re
from threading import Thread
from queue import Queue, Empty
import ctypes
import tkinter as tk
import customtkinter as ctk
from comfy_venvtools import ComfyVenvTools, PYPI_MIRRORS
import shutil

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

class ComfyUIEnvironmentManager(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("ComfyUI中Python环境维护小工具 V2.5 练老师 QQ群: 723799422")
        self.geometry("1100x650")
        self.minsize(1000, 600)

        # 关闭标志，用于优雅退出
        self._closing = False

        # 数据
        self.config_file = os.path.join(os.getcwd(), 'config.json')
        self.python_paths = []
        self.python_exe_path = ""
        self.selected_mirror = '阿里云'
        self.requirements_path = ""
        self.custom_nodes_history = []
        self.plugin_history = []
        self.lib_history = []  # 第三方库名称历史记录
        self.cmd_history = []  # CMD命令历史记录
        self.comfy_paths_history = []  # ComfyUI路径历史记录
        self.progress_var = ctk.DoubleVar(value=0.0)
        self._ui_queue = Queue()  # 主线程刷新队列

        # 后端工具
        self.tools = ComfyVenvTools(self.update_result_text)

        self._init_data()
        self._build_ui()
        self.load_config()
        # 绑定关闭事件，退出前保存配置
        try:
            self.protocol("WM_DELETE_WINDOW", self._on_close)
        except Exception:
            pass
        # 启动时居中显示窗口
        try:
            self._center_on_screen()
        except Exception:
            pass
        # 初始化UI队列并在主线程周期性刷新，保证子线程不直接操作Tk
        self._ui_queue: Queue = Queue()
        try:
            self.after(50, self._drain_ui_queue)
        except Exception:
            pass

    # ---------------- 数据初始化 ----------------
    def _init_data(self):
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                    self.python_paths = [p for p in cfg.get('python_paths', []) if os.path.exists(p)]
                    self.selected_mirror = cfg.get('fastest_mirror', self.selected_mirror)
            except Exception:
                pass
        if not self.python_paths:
            cands = [
                os.path.join(os.getcwd(), 'python.exe'),                  
            ]
            self.python_paths = [p for p in cands if os.path.exists(p)]
        if self.python_paths:
            self.python_exe_path = self.python_paths[0]
        # 依赖下拉的值缓存，用于“追加模式”更新
        self._deps_values_cache: list[str] = []

    # ---------------- UI构建 ----------------
    def _build_ui(self):
        self.main = ctk.CTkFrame(self)
        self.main.pack(fill='both', expand=True, padx=2, pady=2)

        try:
            if not hasattr(self, 'mirror_var') or self.mirror_var is None:
                self.mirror_var = ctk.StringVar(value=self.selected_mirror)
        except Exception:
            self.mirror_var = ctk.StringVar(value=self.selected_mirror)

        # 顶部工具栏已撤消，改回原区域内的版本维护设计

        self.paned = ctk.CTkFrame(self.main)
        self.paned.pack(fill='both', expand=True)
        
        self.left = ctk.CTkFrame(self.paned)
        self.left.pack(side='left', fill='both', expand=False, padx=(0, 2))
        self.left.configure(width=580)
        
        self.right = ctk.CTkFrame(self.paned)
        self.right.pack(side='left', fill='both', expand=True, padx=(2, 0))

        # 左侧六大区域
        self._build_left_sections()

        # 右侧结果显示
        self._build_right_panel()

        # 底部进度条
        self.progress_bar = ctk.CTkProgressBar(self.main)
        self.progress_bar.set(0)
        self.progress_bar.pack(fill='x', pady=(6, 0))

    def _section(self, parent, title):
        frame = ctk.CTkFrame(parent)
        frame.pack(fill='x', padx=2, pady=2)
        ctk.CTkLabel(frame, text=title, font=("Microsoft YaHei", 14, 'bold')).pack(pady=(4, 2))
        return frame

    def _build_left_sections(self): 
        
        # 1 国内源和Python环境（合并为单行，更紧凑）
        sec1 = self._section(self.left, "国内源和Python环境")
        r1 = ctk.CTkFrame(sec1); r1.pack(fill='x', padx=2, pady=3)
        ctk.CTkLabel(r1, text="镜像:").pack(side='left', padx=2)
        self.mirror_var = ctk.StringVar(value=self.selected_mirror)
        self.mirror_cb = ctk.CTkComboBox(r1, variable=self.mirror_var, values=list(PYPI_MIRRORS.keys()), width=80, command=self.on_mirror_change)
        self.mirror_cb.pack(side='left', padx=2)
        # 点击下拉触发测速
        self.mirror_cb.bind("<Button-1>", self._on_mirror_dropdown_click)
        self.python_env_var = ctk.StringVar(value=self.python_exe_path)
        self.python_env_cb = ctk.CTkComboBox(r1, variable=self.python_env_var, values=self.python_paths, width=180, command=self.on_python_env_change)
        self.python_env_cb.pack(side='left', fill='x', expand=True, padx=2)
        ctk.CTkButton(r1, text="添加", width=50, command=self.select_python_environment).pack(side='left', padx=2)
        ctk.CTkButton(r1, text="删除", width=50, command=self.delete_python_environment).pack(side='left', padx=2)

        # 2 插件环境维护
        sec2 = self._section(self.left, "插件维护")
        s2r1 = ctk.CTkFrame(sec2); s2r1.pack(fill='x', padx=2, pady=2)
        ctk.CTkLabel(s2r1, text="CustomNodes目录:").pack(side='left')
        self.custom_nodes_var = ctk.StringVar()
        # 改为下拉列表，来源于历史记录；选择变化时保存并可触发扫描
        self.custom_nodes_cb = ctk.CTkComboBox(s2r1, variable=self.custom_nodes_var, values=self.custom_nodes_history, width=120, command=self.on_custom_nodes_change)
        self.custom_nodes_cb.pack(side='left', fill='x', expand=True, padx=2)
        ctk.CTkButton(s2r1, text="浏览", width=50, command=self.add_customnodes_dir).pack(side='left', padx=2)
        ctk.CTkButton(s2r1, text="删除", width=50, command=self.delete_customnodes_dir).pack(side='left', padx=2)

        s2r2 = ctk.CTkFrame(sec2); s2r2.pack(fill='x', padx=2, pady=2)
        ctk.CTkLabel(s2r2, text="依赖列表:").pack(side='left')
        self.deps_list_var = ctk.StringVar()
        self.deps_list_cb = ctk.CTkComboBox(s2r2, variable=self.deps_list_var, values=[], width=50, command=self.on_deps_file_selected)
        self.deps_list_cb.pack(side='left', fill='x', expand=True, padx=2)
        ctk.CTkButton(s2r2, text="检测依赖", width=50, command=self.detect_dependencies).pack(side='left', padx=2)
        ctk.CTkButton(s2r2, text="手动添加", width=50, command=self.manual_add_requirements).pack(side='left', padx=2)

        # Git 克隆插件行
        s2r3 = ctk.CTkFrame(sec2); s2r3.pack(fill='x', padx=2, pady=2)
        ctk.CTkLabel(s2r3, text="Git Clone 插件地址:").pack(side='left')
        self.git_url_var = ctk.StringVar()
        # 改用下拉列表，来源于历史记录，可在克隆后自动加入
        self.git_url_cb = ctk.CTkComboBox(s2r3, variable=self.git_url_var, values=self.plugin_history, width=200)
        self.git_url_cb.pack(side='left', fill='x', expand=True, padx=2)
        ctk.CTkButton(s2r3, text="安装", width=40, command=self.clone_plugin_into_customnodes).pack(side='left', padx=2)
        ctk.CTkButton(s2r3, text="更新", width=40, command=self.check_plugin_updates).pack(side='left', padx=2)
        ctk.CTkButton(s2r3, text="刷新", width=40, command=self.refresh_git_plugin_list).pack(side='left', padx=2)      

        # 3 Comfy环境操作
        sec3 = self._section(self.left, "ComfyUI环境操作")
        s3r1 = ctk.CTkFrame(sec3); s3r1.pack(fill='x', padx=2, pady=2)
        self.skip_check_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(s3r1, text="跳过已安装检测", variable=self.skip_check_var).pack(side='left')
        
        s3grid = ctk.CTkFrame(sec3); s3grid.pack(fill='x', padx=2, pady=2)
        buttons = [
            ("依赖情况", self.start_checking),
            ("模拟安装", self.start_simulation),
            ("实际安装", self.start_installation),
            ("查找冲突", self.find_conflicting_libraries),
            ("比较环境", self.compare_environment_files),
            ("查看环境", self.view_current_env),
            ("环境迁移", self.start_environment_migration),
            ("环境备份", self.backup_environment_files),
            ("目录还原", self.restore_environment_files),
            ("库列表还原", self.restore_from_env_list),
        ]
        for i in range(5):
            try:
                s3grid.grid_columnconfigure(i, weight=1, uniform="envops")
            except Exception:
                pass
        for i in range(2):
            try:
                s3grid.grid_rowconfigure(i, weight=1)
            except Exception:
                pass
        for idx, (text, fn) in enumerate(buttons):
            r = idx // 5
            c = idx % 5
            btn = ctk.CTkButton(s3grid, text=text, command=fn, width=90)
            btn.grid(row=r, column=c, padx=6, pady=6, sticky="n")
            if fn == self.backup_environment_files:
                self.backup_button = btn
            elif fn == self.restore_environment_files:
                self.restore_button = btn
        
        # 4 第三方库管理
        sec4 = self._section(self.left, "第三方库管理")
        s4r1 = ctk.CTkFrame(sec4); s4r1.pack(fill='x', padx=2, pady=2)
        ctk.CTkLabel(s4r1, text="环境库名:").pack(side='left')
        self.lib_name_var = ctk.StringVar()
        # 使用下拉列表框替代文本输入框，支持历史记录
        self.lib_name_cb = ctk.CTkComboBox(s4r1, variable=self.lib_name_var, values=self.lib_history, width=200)
        self.lib_name_cb.pack(side='left', fill='x', expand=True, padx=6)
        # 绑定回车键事件，方便快速执行
        self.lib_name_cb.bind('<Return>', lambda e: self.search_library_exact())
        ctk.CTkLabel(s4r1, text="版本号:").pack(side='left', padx=6)
        self.version_var = ctk.StringVar()
        self.version_cb = ctk.CTkComboBox(s4r1, variable=self.version_var, values=[], width=100)
        self.version_cb.pack(side='left')
        s4r2 = ctk.CTkFrame(sec4); s4r2.pack(fill='x', padx=2, pady=2)
        # 按钮并排一行，更紧凑
        for text, fn in [("精准查找", self.search_library_exact), ("模糊查找", self.search_library_local), ("安装库", self.install_library), ("删除库", self.uninstall_library), ("轮子安装", self.install_whl_file), ("编译安装", self.install_source_code), ("含库名插件", self.find_plugins_with_library), ("清空信息", self.clear_results)]:
            ctk.CTkButton(s4r2, text=text, width=55, command=fn).pack(side='left', padx=2, pady=2)

        # 5 Python手动执行集合和CMD其他命令
        sec5 = self._section(self.left, "Python手动执行命令和CMD其他命令")
        s5r1 = ctk.CTkFrame(sec5); s5r1.pack(fill='x', padx=2, pady=2)
        ctk.CTkLabel(s5r1, text="CMD:").pack(side='left')
        self.cmd_var = ctk.StringVar()
        # 使用下拉列表框替代文本输入框，支持历史记录
        self.cmd_cb = ctk.CTkComboBox(s5r1, variable=self.cmd_var, values=self.cmd_history, width=300)
        self.cmd_cb.pack(side='left', fill='x', expand=True, padx=2)
        # 绑定回车键事件，方便快速执行
        self.cmd_cb.bind('<Return>', lambda e: self.execute_command())
        ctk.CTkButton(s5r1, text="命令执行", width=50, command=self.execute_command).pack(side='left', padx=2)
        ctk.CTkButton(s5r1, text="参数说明", width=50, command=self.show_pip_params).pack(side='left')

        # 6 Comfy版本维护
        sec6 = self._section(self.left, "ComfyUI便捷版_版本维护")
        s6r1 = ctk.CTkFrame(sec6); s6r1.pack(fill='x', padx=2, pady=2)
        ctk.CTkLabel(s6r1, text="选择目录:").pack(side='left')
        self.comfy_dir_var = ctk.StringVar()
        try:
            initial_paths = list(self.comfy_paths_history) if isinstance(self.comfy_paths_history, list) else []
        except Exception:
            initial_paths = []
        self.comfy_dir_cb = ctk.CTkComboBox(s6r1, variable=self.comfy_dir_var, values=initial_paths, width=380)
        self.comfy_dir_cb.pack(side='left', fill='x', expand=True, padx=2)
        ctk.CTkButton(s6r1, text="浏览", width=50, command=lambda: self._browse_dir(self.comfy_dir_var, self._get_python_parent_dir())).pack(side='left', padx=2)
        ctk.CTkButton(s6r1, text="管理", width=50, command=self._stub_version_manage).pack(side='left', padx=2)

    def _build_right_panel(self):
        ctk.CTkLabel(self.right, text="执行结果", font=("Microsoft YaHei", 14, 'bold')).pack(fill='x', pady=(6, 4))
        self.result_text = ctk.CTkTextbox(self.right, wrap='word')
        self.result_text.pack(fill='both', expand=True, padx=2, pady=2)

    def _center_on_screen(self):
        """使用设计尺寸立即居中，避免绘制延迟"""
        w, h = 1100, 650
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    # -------------- 主线程UI刷新支持 --------------
    def _drain_ui_queue(self):
        # 如果正在关闭，停止处理UI队列
        if getattr(self, '_closing', False):
            return
            
        try:
            while True:
                item = self._ui_queue.get_nowait()
                kind = item[0]
                if kind == 'text':
                    try:
                        self.update_result_text(item[1])
                    except Exception:
                        pass
                elif kind == 'deps_values':
                    try:
                        values = item[1] or []
                        self.deps_list_cb.configure(values=values)
                        if values:
                            self.deps_list_var.set(values[0])
                        # 缓存当前值集合
                        self._deps_values_cache = list(values)
                    except Exception:
                        pass
                elif kind == 'deps_values_append':
                    try:
                        new_vals = list(item[1] or [])
                        existing = list(getattr(self, '_deps_values_cache', []) or [])
                        merged = existing + [v for v in new_vals if v not in existing]
                        self.deps_list_cb.configure(values=merged)
                        # 更新缓存，但不强制改变当前选择
                        self._deps_values_cache = merged
                    except Exception:
                        pass
                elif kind == 'deps_select':
                    try:
                        sel = item[1]
                        if sel:
                            self.deps_list_var.set(sel)
                    except Exception:
                        pass
                elif kind == 'progress':
                    try:
                        # value 0~1 -> 0~100
                        self.progress_bar.set(float(item[1]))
                    except Exception:
                        pass
                elif kind == 'progress_hide':
                    try:
                        self.progress_bar.pack_forget()
                    except Exception:
                        pass
                elif kind == 'progress_show':
                    try:
                        self.progress_bar.pack(fill='x', pady=(8, 0))
                        try:
                            v = float(item[1] or 0.0)
                        except Exception:
                            v = 0.0
                        self.progress_bar.set(v)
                    except Exception:
                        pass
                elif kind == 'update_version_list':
                    try:
                        item[1]()  # 执行更新函数
                    except Exception:
                        pass
                elif kind == 'update_error':
                    try:
                        item[1]()  # 执行错误处理函数
                    except Exception:
                        pass
        except Empty:
            pass
        finally:
            # 只有在不关闭的情况下才继续调度
            if not getattr(self, '_closing', False):
                try:
                    self.after(50, self._drain_ui_queue)
                except Exception:
                    pass

    def _enqueue_text(self, text: str):
        try:
            if text:
                self._ui_queue.put(('text', text))
        except Exception:
            pass

    def _enqueue_deps_values(self, values):
        try:
            self._ui_queue.put(('deps_values', list(values or [])))
        except Exception:
            pass

    def _enqueue_deps_values_append(self, values):
        try:
            self._ui_queue.put(('deps_values_append', list(values or [])))
        except Exception:
            pass

    def _enqueue_deps_select(self, path: str):
        try:
            if path:
                self._ui_queue.put(('deps_select', path))
        except Exception:
            pass

    def _enqueue_progress(self, value: float):
        try:
            # 统一使用 'progress' 事件键，_drain_ui_queue 中会调用 progress_bar.set
            self._ui_queue.put(('progress', value))
        except Exception:
            pass

    def _enqueue_progress_hide(self):
        try:
            self._ui_queue.put(('progress_hide', None))
        except Exception:
            pass

    def _enqueue_progress_show(self, value: float = 0.0):
        try:
            self._ui_queue.put(('progress_show', value))
        except Exception:
            pass

    def _get_available_drives(self):
        """获取可用的Windows驱动器列表"""
        try:
            import string
            import win32api
            import ctypes
            from ctypes import wintypes
            
            drives = []
            bitmask = win32api.GetLogicalDrives()
            
            # 定义驱动器类型常量
            DRIVE_REMOVABLE = 2
            DRIVE_FIXED = 3
            DRIVE_REMOTE = 4
            DRIVE_CDROM = 5
            DRIVE_RAMDISK = 6
            
            for i, letter in enumerate(string.ascii_uppercase):
                if bitmask & (1 << i):
                    drive_name = f"{letter}:"
                    try:
                        # 获取驱动器类型
                        drive_type = ctypes.windll.kernel32.GetDriveTypeW(f"{letter}:\\")
                        type_name = {
                            DRIVE_REMOVABLE: "可移动",
                            DRIVE_FIXED: "本地磁盘", 
                            DRIVE_REMOTE: "网络",
                            DRIVE_CDROM: "CD-ROM",
                            DRIVE_RAMDISK: "RAM磁盘"
                        }.get(drive_type, "未知")
                        
                        # 获取卷标
                        try:
                            volume_name = win32api.GetVolumeInformation(f"{letter}:\\")[0]
                            if volume_name:
                                drives.append(f"{drive_name} [{volume_name}] - {type_name}")
                            else:
                                drives.append(f"{drive_name} - {type_name}")
                        except:
                            drives.append(f"{drive_name} - {type_name}")
                    except:
                        drives.append(f"{drive_name}")
            return drives if drives else ["C: - 本地磁盘"]
        except Exception as e:
            print(f"Drive detection error: {e}")
            # 如果win32api不可用，返回基本驱动器
            return ["C: - 本地磁盘", "D: - 本地磁盘", "E: - 本地磁盘"]
    
    def _parse_drive_from_display(self, drive_display):
        """从显示文本解析驱动器字母"""
        if drive_display and len(drive_display) >= 2:
            return drive_display[0] + ":"
        return "C:"
    
    # ---------------- 暗色调文件选择对话框 ----------------
    def _create_dark_file_dialog(self, title="选择文件", dialog_type="open", filetypes=None, defaultextension=None, initialfile=None, starting_dir=None):
        """
        创建暗色调文件选择对话框
        dialog_type: "open", "save", "directory"
        """
        dialog = ctk.CTkToplevel(self)
        dialog.title(title)
        dialog.geometry("700x600")  # 增加高度
        dialog.transient(self)
        dialog.grab_set()
        
        # 设置暗色标题栏
        self._set_dark_titlebar(dialog)
        
        # 居中显示
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - 700) // 2
        y = (dialog.winfo_screenheight() - 600) // 2  # 更新居中计算
        dialog.geometry(f"+{x}+{y}")
        
        # 当前路径变量（支持起始目录）
        try:
            start_dir = starting_dir if (starting_dir and os.path.isdir(starting_dir)) else os.getcwd()
        except Exception:
            start_dir = os.getcwd()
        current_path = ctk.StringVar(value=start_dir)
        selected_item = ctk.StringVar()
        
        # 获取可用驱动器
        available_drives = self._get_available_drives()
        current_drive = ctk.StringVar(value=available_drives[0] if available_drives else "C:")
        
        # 顶部控制栏（驱动器选择 + 路径栏）
        top_frame = ctk.CTkFrame(dialog)
        top_frame.pack(fill='x', padx=10, pady=(10, 5))
        
        # 驱动器选择下拉框
        ctk.CTkLabel(top_frame, text="驱动器:").pack(side='left', padx=(0, 5))
        drive_combo = ctk.CTkComboBox(top_frame, values=available_drives, variable=current_drive, width=200, height=30)
        drive_combo.pack(side='left', padx=(0, 10))
        
        def on_drive_change(choice):
            # 解析驱动器字母
            drive_letter = self._parse_drive_from_display(choice)
            drive_path = f"{drive_letter}\\"
            if os.path.exists(drive_path):
                current_path.set(drive_path)
                refresh_file_list()
                selected_item.set("")
                update_selection_label()
        
        drive_combo.configure(command=on_drive_change)
        
        # 路径栏
        ctk.CTkLabel(top_frame, text="路径:").pack(side='left', padx=(0, 5))
        path_entry = ctk.CTkEntry(top_frame, textvariable=current_path, height=30)
        path_entry.pack(side='left', fill='x', expand=True, padx=(0, 5))
        
        def navigate_to_path():
            try:
                path = current_path.get()
                if os.path.exists(path) and os.path.isdir(path):
                    refresh_file_list()
                    selected_item.set("")
                    update_selection_label()
                else:
                    self._show_dark_warning("路径错误", "指定的路径不存在或不是目录")
            except Exception as e:
                self._show_dark_warning("路径错误", f"无法访问路径: {e}")
        
        ctk.CTkButton(top_frame, text="跳转", width=60, command=navigate_to_path).pack(side='left')
        
        # 快捷按钮栏（紧凑布局）
        quick_frame = ctk.CTkFrame(dialog)
        quick_frame.pack(fill='x', padx=10, pady=(0, 5))
        
        def create_new_directory():
            """在当前目录中创建新子目录"""
            current_dir = current_path.get()
            if not os.path.exists(current_dir) or not os.path.isdir(current_dir):
                self._show_dark_warning("创建目录失败", "当前路径无效或不存在")
                return
            
            # 创建输入对话框
            input_dialog = ctk.CTkToplevel(dialog)
            input_dialog.title("新建目录")
            input_dialog.geometry("400x150")
            input_dialog.transient(dialog)
            input_dialog.grab_set()
            
            # 设置暗色标题栏
            self._set_dark_titlebar(input_dialog)
            
            # 居中显示
            input_dialog.update_idletasks()
            x = (input_dialog.winfo_screenwidth() - input_dialog.winfo_width()) // 2
            y = (input_dialog.winfo_screenheight() - input_dialog.winfo_height()) // 2
            input_dialog.geometry(f"+{x}+{y}")
            
            # 创建界面
            main_frame = ctk.CTkFrame(input_dialog)
            main_frame.pack(fill='both', expand=True, padx=15, pady=15)
            
            ctk.CTkLabel(main_frame, text="请输入新目录名称:", font=ctk.CTkFont(size=12, weight="bold")).pack(pady=5)
            
            dir_name_var = ctk.StringVar()
            name_entry = ctk.CTkEntry(main_frame, textvariable=dir_name_var, width=250)
            name_entry.pack(pady=5)
            name_entry.focus()
            
            def create_directory():
                dir_name = dir_name_var.get().strip()
                if not dir_name:
                    self._show_dark_warning("输入错误", "目录名称不能为空")
                    return
                
                # 检查目录名称是否包含非法字符
                invalid_chars = '<>:"/\\|?*'
                if any(char in dir_name for char in invalid_chars):
                    self._show_dark_warning("输入错误", f"目录名称不能包含以下字符:\n{invalid_chars}")
                    return
                
                new_dir_path = os.path.join(current_dir, dir_name)
                
                try:
                    if os.path.exists(new_dir_path):
                        self._show_dark_warning("创建失败", f"目录已存在:\n{dir_name}")
                        return
                    
                    os.makedirs(new_dir_path)
                    input_dialog.destroy()
                    refresh_file_list()
                    self._text_enqueue(f"[新建目录] ✅ 创建目录成功: {dir_name}")
                    
                except Exception as e:
                    self._show_dark_warning("创建失败", f"无法创建目录:\n{str(e)}")
            
            def cancel_creation():
                input_dialog.destroy()
            
            # 按钮区域
            button_frame = ctk.CTkFrame(main_frame)
            button_frame.pack(pady=10)
            
            ctk.CTkButton(button_frame, text="创建", command=create_directory, width=80, fg_color="green", hover_color="dark green").pack(side='left', padx=5)
            ctk.CTkButton(button_frame, text="取消", command=cancel_creation, width=80).pack(side='left', padx=5)
            
            # 绑定回车键
            name_entry.bind('<Return>', lambda e: create_directory())
            
            # 等待对话框关闭
            input_dialog.wait_window(input_dialog)
        
        def go_desktop():
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            if os.path.exists(desktop):
                current_path.set(desktop)
                refresh_file_list()
                selected_item.set("")
                update_selection_label()
        
        def go_up():
            parent = os.path.dirname(current_path.get())
            if os.path.exists(parent):
                current_path.set(parent)
                refresh_file_list()
                selected_item.set("")
                update_selection_label()
        
        # 使用更紧凑的按钮
        ctk.CTkButton(quick_frame, text="📁 新建目录", width=60, command=create_new_directory).pack(side='left', padx=(0, 3))
        ctk.CTkButton(quick_frame, text="🖥️ 桌面", width=60, command=go_desktop).pack(side='left', padx=(0, 3))
        ctk.CTkButton(quick_frame, text="⬆️ 上级", width=60, command=go_up).pack(side='left')
        
        # 文件列表框架
        list_frame = ctk.CTkFrame(dialog)
        list_frame.pack(fill='both', expand=True, padx=10, pady=5)
        
        # 文件列表（使用文本框模拟列表框）
        file_text = ctk.CTkTextbox(list_frame, width=680, height=300)
        file_text.pack(fill='both', expand=True, padx=5, pady=5)
        try:
            file_text.tag_configure("selected_line", background="#1f4f99")
        except Exception:
            pass

        sel_frame = ctk.CTkFrame(dialog)
        sel_frame.pack(fill='x', padx=10, pady=(0, 8))
        sel_label_var = ctk.StringVar(value="")
        sel_label = ctk.CTkLabel(sel_frame, textvariable=sel_label_var)
        sel_label.pack(side='left')

        def update_selection_label():
            try:
                if dialog_type == "directory":
                    sel_label_var.set(f"当前目录: {current_path.get()}")
                else:
                    if selected_item.get():
                        sel_label_var.set(f"已选择文件: {os.path.join(current_path.get(), selected_item.get())}")
                    else:
                        sel_label_var.set("已选择文件: (未选择)")
            except Exception:
                pass

        update_selection_label()
        
        def refresh_file_list():
            file_text.delete('1.0', 'end')
            try:
                file_text.tag_remove("selected_line", '1.0', 'end')
            except Exception:
                pass
            current_dir = current_path.get()
            
            try:
                items = []
                
                # 如果不是驱动器根目录，添加"返回上级"选项
                if not current_dir.endswith(':\\') and current_dir != '/':
                    items.append("⬆️ 返回上级目录")
                
                # 添加目录
                for item in sorted(os.listdir(current_dir)):
                    item_path = os.path.join(current_dir, item)
                    if os.path.isdir(item_path):
                        items.append(f"📁 {item}")
                
                # 添加文件（根据对话框类型和文件类型过滤）
                for item in sorted(os.listdir(current_dir)):
                    item_path = os.path.join(current_dir, item)
                    if os.path.isfile(item_path):
                        # 文件类型过滤
                        if dialog_type == "directory":
                            continue  # 目录选择不显示文件
                        elif filetypes and dialog_type in ["open", "save"]:
                            # 简单的文件扩展名过滤
                            ext = os.path.splitext(item)[1].lower()
                            allowed = False
                            for desc, pattern in filetypes:
                                if pattern == "*.*":
                                    allowed = True
                                    break
                                elif ext in pattern.lower():
                                    allowed = True
                                    break
                            if not allowed:
                                continue
                        
                        items.append(f"📄 {item}")
                
                # 显示项目
                for item in items:
                    file_text.insert('end', item + '\n')
                
            except Exception as e:
                file_text.insert('end', f"无法读取目录: {e}\n")
        
        def on_item_click(event):
            # 获取点击的行
            index = file_text.index(f"@{event.x},{event.y}")
            line = file_text.get(f"{index} linestart", f"{index} lineend").strip()
            try:
                file_text.tag_remove("selected_line", '1.0', 'end')
                line_num = int(str(index).split('.')[0])
                file_text.tag_add("selected_line", f"{line_num}.0", f"{line_num}.end")
            except Exception:
                pass
            
            if line:
                # 处理特殊选项
                if line == "⬆️ 返回上级目录":
                    parent = os.path.dirname(current_path.get())
                    if os.path.exists(parent):
                        current_path.set(parent)
                        refresh_file_list()
                    return
                
                # 提取项目名称（移除图标）
                item_name = line[2:] if line.startswith(("📁", "📄")) else line
                item_path = os.path.join(current_path.get(), item_name)
                
                if os.path.isdir(item_path):
                    # 进入目录
                    current_path.set(item_path)
                    refresh_file_list()
                    selected_item.set("")
                    update_selection_label()
                elif os.path.isfile(item_path):
                    # 选择文件
                    selected_item.set(item_name)
                    if dialog_type == "save":
                        filename_entry.delete(0, 'end')
                        filename_entry.insert(0, item_name)
                    update_selection_label()
        
        file_text.bind('<Button-1>', on_item_click)
        
        # 文件名输入（仅用于保存对话框）
        if dialog_type == "save":
            filename_frame = ctk.CTkFrame(dialog)
            filename_frame.pack(fill='x', padx=10, pady=5)
            ctk.CTkLabel(filename_frame, text="文件名:").pack(side='left', padx=(0, 5))
            filename_entry = ctk.CTkEntry(filename_frame, height=30)
            filename_entry.pack(side='left', fill='x', expand=True)
            if initialfile:
                filename_entry.insert(0, initialfile)
        
        # 按钮框架
        button_frame = ctk.CTkFrame(dialog)
        button_frame.pack(fill='x', padx=10, pady=(5, 10))
        
        def on_ok():
            if dialog_type == "directory":
                result = current_path.get()
            elif dialog_type == "save":
                filename = filename_entry.get().strip()
                if not filename:
                    self._show_dark_warning("输入错误", "请输入文件名")
                    return
                # 添加默认扩展名
                if defaultextension and not os.path.splitext(filename)[1]:
                    filename += defaultextension
                result = os.path.join(current_path.get(), filename)
            else:  # open
                if not selected_item.get():
                    self._show_dark_warning("选择错误", "请选择一个文件")
                    return
                result = os.path.join(current_path.get(), selected_item.get())
            
            dialog.result = result
            dialog.destroy()
        
        def on_cancel():
            dialog.result = None
            dialog.destroy()
        
        ctk.CTkButton(button_frame, text="取消", command=on_cancel, width=100).pack(side='right', padx=(5, 0))
        ctk.CTkButton(button_frame, text="确定", command=on_ok, width=100).pack(side='right')
        
        # 初始化文件列表
        refresh_file_list()
        
        # 等待对话框关闭
        self.wait_window(dialog)
        return getattr(dialog, 'result', None)
    
    def _ask_directory_dark(self, title="选择目录", starting_dir=None):
        return self._create_dark_file_dialog(title=title, dialog_type="directory", filetypes=None, defaultextension=None, initialfile=None, starting_dir=starting_dir)
    
    def _ask_open_filename_dark(self, title="选择文件", filetypes=None):
        """暗色调文件打开对话框"""
        return self._create_dark_file_dialog(title=title, dialog_type="open", filetypes=filetypes)
    
    def _ask_saveas_filename_dark(self, title="保存文件", filetypes=None, defaultextension=None, initialfile=None):
        """暗色调文件保存对话框"""
        return self._create_dark_file_dialog(title=title, dialog_type="save", filetypes=filetypes, 
                                           defaultextension=defaultextension, initialfile=initialfile)
    
    # ---------------- 通用动作 ----------------
    def _browse_dir(self, var, starting_dir=None):
        path = self._ask_directory_dark(title="选择目录", starting_dir=starting_dir)
        if path:
            var.set(path)
            try:
                if var is self.comfy_dir_var:
                    paths = [p for p in self.comfy_paths_history if p != path]
                    paths.insert(0, path)
                    self.comfy_paths_history = paths[:20]
                    try:
                        if hasattr(self, 'comfy_dir_cb'):
                            self.comfy_dir_cb.configure(values=self.comfy_paths_history)
                    except Exception:
                        pass
                    self.save_config()
            except Exception:
                pass

    def _get_python_parent_dir(self):
        try:
            if self.python_exe_path:
                base = os.path.dirname(self.python_exe_path)
                return os.path.dirname(base)
        except Exception:
            pass
        return os.getcwd()

    # comfyui路径列表框已移除

    def update_result_text(self, text):
        self.result_text.insert('end', text + "\n")
        self.result_text.see('end')

    def clear_results(self):
        self.result_text.delete('0.0', 'end')

    def save_config(self):
        try:
            cfg = {
                'python_paths': self.python_paths,
                'current_python_exe': self.python_exe_path,
                'fastest_mirror': self.mirror_var.get(),
                'custom_nodes_dir': self.custom_nodes_var.get(),
                'requirements_cache': list(getattr(self, 'requirements_cache', set())),
                'custom_nodes_history': self.custom_nodes_history,
                'plugin_history': self.plugin_history,
                'lib_history': self.lib_history,  # 第三方库历史记录
                'cmd_history': self.cmd_history,  # CMD命令历史记录
                'comfy_paths_history': self.comfy_paths_history,
                '_missing_cache':   {k: v for k, v in getattr(self, '_missing_cache', {}).items()}
            }
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.update_result_text(f"保存配置失败: {e}")

    def load_config(self):
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                self.python_paths = [p for p in cfg.get('python_paths', []) if os.path.exists(p)]
                self.selected_mirror = cfg.get('fastest_mirror', self.selected_mirror)
                self.mirror_var.set(self.selected_mirror)
                # 恢复当前选择的Python环境
                cur_py = cfg.get('current_python_exe', '')
                if cur_py and os.path.exists(cur_py) and cur_py not in self.python_paths:
                    self.python_paths.insert(0, cur_py)
                self.python_env_cb.configure(values=self.python_paths)
                if cur_py and os.path.exists(cur_py):
                    self.python_exe_path = cur_py
                elif self.python_paths:
                    self.python_exe_path = self.python_paths[0]
                else:
                    self.python_exe_path = ""
                self.python_env_var.set(self.python_exe_path)
                # 同步到后端，确保后续操作使用该环境
                try:
                    if self.python_exe_path:
                        self.tools.set_python_env(self.python_exe_path)
                except Exception:
                    pass
                # 加载插件目录与缓存
                self.custom_nodes_var.set(cfg.get('custom_nodes_dir', ''))
                self.requirements_cache = set(cfg.get('requirements_cache', []))
                self.comfy_paths_history = cfg.get('comfy_paths_history', [])
                # 加载插件目录历史
                self.custom_nodes_history = cfg.get('custom_nodes_history', [])
                try:
                    self.custom_nodes_cb.configure(values=self.custom_nodes_history)
                except Exception:
                    pass
                # 加载第三方插件地址历史记录
                self.plugin_history = cfg.get('plugin_history', [])
                # 在列表开头添加空行，方便用户选择
                display_history = [''] + self.plugin_history
                try:
                    self.git_url_cb.configure(values=display_history)
                except Exception:
                    pass
                # 加载第三方库历史记录
                self.lib_history = cfg.get('lib_history', [])
                # 加载CMD命令历史记录
                self.cmd_history = cfg.get('cmd_history', [])
                # 加载上次检测缓存
                self._missing_cache = {k: v for k, v in cfg.get('_missing_cache', {}).items()}
                try:
                    if hasattr(self, 'comfy_dir_cb'):
                        self.comfy_dir_cb.configure(values=self.comfy_paths_history)
                    if self.comfy_paths_history:
                        self.comfy_dir_var.set(self.comfy_paths_history[0])
                except Exception:
                    pass
            # 若启动时已有插件目录，触发一次选择事件，恢复下拉列表
            init_path = self.custom_nodes_var.get()
            if init_path:
                self.after(50, lambda: self.on_custom_nodes_change())
        except Exception as e:
            self.update_result_text(f"加载配置失败: {e}")

    def _on_close(self):
        """窗口关闭时保存当前选择并退出。"""
        try:
            # 设置关闭标志，停止新的定时器调度
            self._closing = True

            # 如果有正在运行的robocopy，立即终止
            try:
                if hasattr(self, '_robocopy_proc') and self._robocopy_proc:
                    proc = self._robocopy_proc
                    if proc and proc.poll() is None:
                        try:
                            proc.terminate()
                        except Exception:
                            try:
                                subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)], capture_output=True)
                            except Exception:
                                pass
            except Exception:
                pass
            
            # 保存配置
            self.save_config()
            
            # 清空UI队列，避免关闭时还有未处理的任务
            try:
                while not self._ui_queue.empty():
                    self._ui_queue.get_nowait()
            except (Queue.Empty, AttributeError):
                pass
                
        except Exception:
            pass
        
        # 确保窗口被正确销毁
        try:
            self.destroy()
        except Exception:
            # 如果destroy失败，尝试强制退出
            try:
                import sys
                sys.exit(0)
            except Exception:
                pass

    def _add_to_lib_history(self, lib_name: str):
        """将库名称添加到历史记录中"""
        try:
            if not lib_name:
                return
            # 去重，最近使用排前
            self.lib_history = [lib for lib in self.lib_history if lib != lib_name]
            self.lib_history.insert(0, lib_name)
            try:
                self.lib_name_cb.configure(values=self.lib_history)
            except Exception:
                pass
            self.save_config()
        except Exception:
            pass

    def backup_environment_files(self):
        """备份环境文件 - 支持OS极速模式"""
        try:
            self._text_enqueue("[备份] 🚀 开始备份流程...")
            
            python_exe = self.python_exe_path
            if not python_exe or not os.path.exists(python_exe):
                self._show_dark_warning("⚠️ Python环境无效", 
                                        "请先设置有效的Python环境路径！", 
                                        "Python环境路径无效或不存在，无法备份环境文件。\n请先选择或浏览有效的Python环境路径。")
                return
            
            python_dir = os.path.dirname(python_exe)
            self._text_enqueue(f"[备份] Python环境路径: {python_exe}")
            self._text_enqueue(f"[备份] Python目录: {python_dir}")
            
            # 让用户选择备份目录（使用自定义文件对话框）
            backup_root = self._ask_directory_dark("选择备份文件保存目录")
            if not backup_root:
                self._text_enqueue("[备份] 用户取消备份操作")
                return
            
            # 按日期时间创建备份子目录
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"comfyui_env_backup_{timestamp}"
            backup_dir = os.path.join(backup_root, backup_name)
            
            self._text_enqueue(f"[备份] 备份根目录: {backup_root}")
            self._text_enqueue(f"[备份] 备份子目录: {backup_name}")
            self._text_enqueue(f"[备份] 完整备份路径: {backup_dir}")
            
            # 直接使用Windows系统复制命令，不再询问模式
            self._text_enqueue("[备份] 🚀 使用Windows系统复制命令进行备份...")
            self._text_enqueue("[备份] 💡 直接调用系统复制命令，速度最快")
            
            # 初始化备份状态
            self.backup_status = {
                'total_items': 0,
                'backed_up_items': 0,
                'last_progress_update': 0,
                'backup_dir': backup_dir,
                'python_dir': python_dir,
                'completed': False,
                'error': None,
                'use_os_speed_mode': True  # 直接使用系统复制模式
            }
            
            # 禁用备份按钮，防止重复点击
            if hasattr(self, 'backup_button'):
                self.backup_button.configure(state="disabled")
                self._text_enqueue("[备份] 备份按钮已禁用")
            else:
                self._text_enqueue("[备份] ⚠️ 备份按钮引用不存在")
            
            # 直接启动系统复制备份线程
            self._text_enqueue("[备份] 🚀 启动Windows系统复制备份...")
            backup_thread = Thread(target=self._os_speed_backup_worker, 
                                 args=(python_dir, backup_dir), 
                                 daemon=True)
            
            # 启动后台备份线程
            backup_thread.start()
            self._text_enqueue("[备份] ✅ 后台备份线程已启动")
            
            # 启动UI更新定时器
            self._start_backup_ui_update()
            self._text_enqueue("[备份] ✅ UI更新定时器已启动")
            
        except Exception as e:
            self._text_enqueue(f"[备份] ❌ 启动备份失败: {e}")
            self._restore_backup_ui_state()
    
    def _os_speed_backup_worker(self, python_dir, backup_dir):
        """OS极速备份工作线程 - 直接调用系统命令"""
        try:
            self._text_enqueue(f"[极速备份] 🚀 启动OS极速备份模式")
            self._text_enqueue(f"[极速备份] 📁 源目录: {python_dir}")
            self._text_enqueue(f"[极速备份] 💾 目标目录: {backup_dir}")
            
            # 验证目录
            if not os.path.exists(python_dir):
                self._text_enqueue(f"[极速备份] ❌ Python目录不存在: {python_dir}")
                self.backup_status['error'] = f"Python目录不存在: {python_dir}"
                return
            
            # 创建目标目录
            try:
                os.makedirs(backup_dir, exist_ok=True)
                self._text_enqueue(f"[极速备份] ✅ 目标目录创建成功")
            except Exception as e:
                self._text_enqueue(f"[极速备份] ❌ 目标目录创建失败: {e}")
                self.backup_status['error'] = f"目标目录创建失败: {e}"
                return
            
            start_time = time.time()
            
            # 只使用Windows robocopy
            success = self._windows_os_copy(python_dir, backup_dir)
            
            if success and not self._closing:
                elapsed_time = time.time() - start_time
                self._text_enqueue(f"[极速备份] ✅ 备份完成！")
                self._text_enqueue(f"[极速备份] ⏱️ 耗时: {elapsed_time:.1f}秒")
                self._text_enqueue(f"[极速备份] 📁 备份目录: {backup_dir}")
                
                # 显示备份统计信息
                try:
                    total_size = self._get_directory_size(backup_dir)
                    self._text_enqueue(f"[极速备份] 💾 备份大小: {total_size / (1024**3):.2f} GB")
                    if elapsed_time > 0:
                        speed_mbps = (total_size / (1024**2)) / elapsed_time
                        self._text_enqueue(f"[极速备份] 🚀 平均速度: {speed_mbps:.1f} MB/s")
                except Exception:
                    pass
                    
            elif self._closing:
                self._text_enqueue("[极速备份] ⚠️ 备份操作被取消")
            else:
                self._text_enqueue("[极速备份] ❌ robocopy复制失败")
                
        except Exception as e:
            self.backup_status['error'] = str(e)
            self._text_enqueue(f"[极速备份] 备份失败: {e}")
        finally:
            self.backup_status['completed'] = True
    
    def _windows_os_copy(self, src_dir, dst_dir):
        """Windows系统使用robocopy，每50个文件显示进度"""
        try:
            # 路径验证
            src_dir = os.path.normpath(src_dir)
            dst_dir = os.path.normpath(dst_dir)
            
            if not os.path.exists(src_dir):
                self._text_enqueue(f"[系统复制] ❌ 源目录不存在: {src_dir}")
                return False
            
            self._text_enqueue(f"[系统复制] 📁 源目录: {src_dir}")
            self._text_enqueue(f"[系统复制] 💾 目标目录: {dst_dir}")
            
            # 强制立即处理初始消息
            try:
                self._drain_ui_queue()
            except Exception:
                pass
            
            # 先统计总文件数用于显示
            total_files = 0
            for root, dirs, files in os.walk(src_dir):
                total_files += len(files)
            
            self._text_enqueue(f"[系统复制] 📊 总计: {total_files} 个文件")
            
            # 估算平均每目录文件数（用于更准确的进度估算）
            dir_count = 0
            file_count = 0
            try:
                for root, dirs, files in os.walk(src_dir):
                    if len(root.replace(src_dir, '').split(os.sep)) <= 2:  # 只统计前两层
                        dir_count += len(dirs)
                        file_count += len(files)
                avg_files_per_dir = file_count / max(dir_count, 1)
            except:
                avg_files_per_dir = 10  # 默认值
            
            self._text_enqueue(f"[系统复制] 📊 平均每目录文件数: {avg_files_per_dir:.1f}")
            
            # 使用robocopy，每50个文件显示一次进度
            self._text_enqueue(f"[系统复制] 🚀 robocopy开始复制...")
            self._text_enqueue(f"[系统复制] ⏱️ 开始时间: {time.strftime('%H:%M:%S')}")
            self._text_enqueue(f"[系统复制] ⏰ 进度更新间隔: 5秒")
            start_time = time.time()
            
            # 强制立即处理开始消息
            try:
                self._drain_ui_queue()
            except Exception:
                pass
            
            # 优化方案：后台robocopy + 轻量级进度监控
            robocopy_cmd = [
                'robocopy', src_dir, dst_dir, 
                '/E',        # 复制子目录，包括空目录
                '/COPYALL',  # 复制所有文件信息
                '/R:2',      # 重试2次
                '/W:2',      # 等待2秒
                '/NP',       # 无进度百分比（减少输出）
                '/NDL',      # 不记录目录名（减少输出）
                '/NFL'       # 不记录文件名（减少输出）
            ]
            
            self._text_enqueue(f"[系统复制] 📝 执行命令: {' '.join(robocopy_cmd[:3])} ...")
            self._text_enqueue(f"[系统复制] ⚡ 极速模式：最小性能影响")
            try:
                self._enqueue_progress_show(0.0)
            except Exception:
                pass
            
            # 方案：后台线程执行robocopy + 轻量级进度检查
            import threading
            
            copy_completed = False
            copy_error = None
            copy_return_code = -1
            
            def copy_thread():
                nonlocal copy_completed, copy_error, copy_return_code
                try:
                    # 在后台线程中启动robocopy进程，便于程序退出时可终止
                    self._robocopy_proc = subprocess.Popen(
                        robocopy_cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        text=True,
                        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
                    )
                    copy_return_code = self._robocopy_proc.wait()
                    copy_completed = True
                except Exception as e:
                    copy_error = str(e)
                    copy_completed = True
            
            # 启动后台复制线程
            thread = threading.Thread(target=copy_thread, daemon=True)
            thread.start()
            
            self._text_enqueue(f"[系统复制] 🚀 robocopy后台进程已启动")
            start_time = time.time()
            last_files_count = 0
            last_full_scan_time = time.time() - 10.0
            
            while not copy_completed and thread.is_alive():
                time.sleep(5.0)
                now = time.time()
                elapsed = now - start_time
                
                if now - last_full_scan_time >= 10.0:
                    scan_files = 0
                    try:
                        if os.path.exists(dst_dir):
                            for root, dirs, files in os.walk(dst_dir):
                                scan_files += len(files)
                    except Exception:
                        scan_files = last_files_count
                    
                    last_files_count = scan_files
                    last_full_scan_time = now
                    progress_percent = (scan_files / total_files) * 100 if total_files > 0 else 0
                    self._text_enqueue(f"[系统复制] 📈 进度: {scan_files}/{total_files} ({progress_percent:.1f}%) 已用: {elapsed:.1f}秒")
                    try:
                        self.progress_var.set(min(95, progress_percent))
                        self._enqueue_progress(min(0.95, (progress_percent / 100.0)))
                    except Exception:
                        pass
                else:
                    progress_percent = (last_files_count / total_files) * 100 if total_files > 0 else 0
                    try:
                        self.progress_var.set(min(95, progress_percent))
                        self._enqueue_progress(min(0.95, (progress_percent / 100.0)))
                    except Exception:
                        pass
                
                try:
                    self._drain_ui_queue()
                except:
                    pass
            
            # 等待线程完成
            thread.join(timeout=10)  # 最多等待10秒收尾
            
            # 获取最终结果
            total_time = time.time() - start_time
            
            if copy_error:
                self._text_enqueue(f"[系统复制] ⚠️ 复制错误: {copy_error}")
            
            # 最终文件统计
            final_files = 0
            try:
                if os.path.exists(dst_dir):
                    for root, dirs, files in os.walk(dst_dir):
                        final_files += len(files)
            except:
                pass
            
            # 最终文件统计（完整遍历一次）
            final_files = 0
            try:
                if os.path.exists(dst_dir):
                    for root, dirs, files in os.walk(dst_dir):
                        final_files += len(files)
            except:
                final_files = last_files_count  # 使用最后一次的估算值
            
            total_time = time.time() - start_time
            
            if copy_error:
                self._text_enqueue(f"[系统复制] ⚠️ 复制错误: {copy_error}")
            
            # robocopy返回码判断
            success = (copy_return_code <= 7) and not copy_error
            if success:
                self.progress_var.set(100)
                try:
                    self._enqueue_progress(1.0)
                except Exception:
                    pass
                # 简洁的完成信息
                self._text_enqueue(f"[系统复制] ✅ 完成！{final_files} 文件 {total_time:.1f}秒")
                if total_time > 0 and final_files > 0:
                    speed = final_files / total_time
                    self._text_enqueue(f"[系统复制] 🚀 {speed:.1f} 文件/秒")
                # 清理进程句柄
                try:
                    self._robocopy_proc = None
                except Exception:
                    pass
            
            return success
                
        except Exception as e:
            self._text_enqueue(f"[系统复制] ❌ 系统复制异常: {e}")
            return False
    
    def _get_directory_size(self, path):
        """获取目录总大小"""
        total_size = 0
        try:
            for dirpath, dirnames, filenames in os.walk(path):
                for filename in filenames:
                    filepath = os.path.join(dirpath, filename)
                    if os.path.exists(filepath):
                        total_size += os.path.getsize(filepath)
        except Exception:
            pass
        return total_size

    def _start_backup_ui_update(self):
        """启动备份UI更新定时器"""
        try:
            self.backup_ui_update_id = self.after(100, self._update_backup_ui)
            self._text_enqueue("[备份] UI更新定时器已启动")
        except Exception as e:
            self._text_enqueue(f"[备份] ❌ 启动UI更新定时器失败: {e}")
            self._restore_backup_ui_state()

    def _update_backup_ui(self):
        """更新备份UI状态"""
        try:
            if hasattr(self, 'backup_status') and self.backup_status.get('completed'):
                # 备份完成，恢复UI状态
                self._text_enqueue("[备份] 检测到备份完成，正在恢复UI状态...")
                self._restore_backup_ui_state()
                self._enqueue_progress_hide()
                self._text_enqueue("[备份] UI状态恢复完成")
                return
            
            # 继续更新
            if hasattr(self, 'backup_ui_update_id'):
                self.backup_ui_update_id = self.after(100, self._update_backup_ui)
        except Exception as e:
            self._text_enqueue(f"[备份] UI更新出错: {e}")
            self._restore_backup_ui_state()

    def _restore_backup_ui_state(self):
        """恢复备份UI状态"""
        if hasattr(self, 'backup_button'):
            self.backup_button.configure(state="normal")
        
        # 清理备份状态
        if hasattr(self, 'backup_status'):
            del self.backup_status
        
        if hasattr(self, 'backup_ui_update_id'):
            if self.backup_ui_update_id:
                self.after_cancel(self.backup_ui_update_id)
            del self.backup_ui_update_id

    def restore_from_env_list(self):
        """从环境库列表TXT文件还原Python库（从查看环境保存的文件还原）"""
        try:
            python_exe = self.python_exe_path
            if not python_exe or not os.path.exists(python_exe):
                self._show_dark_warning("⚠️ Python环境无效", 
                                        "请先设置有效的Python环境路径！", 
                                        "Python环境路径无效或不存在，无法还原环境库。\n请先选择或浏览有效的Python环境路径。")
                return
            
            # 让用户选择环境库列表TXT文件
            env_file = self._ask_open_filename_dark("选择环境库列表文件 (TXT)", 
                                                     filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
            if not env_file:
                self._text_enqueue("[库列表还原] 用户取消选择文件")
                return
            
            if not os.path.exists(env_file):
                self._show_dark_warning("⚠️ 文件无效", 
                                        "选择的环境库列表文件不存在！", 
                                        f"文件不存在: {env_file}\n请选择有效的环境库列表TXT文件。")
                return
            
            # 读取环境库列表文件内容
            try:
                with open(env_file, 'r', encoding='utf-8', errors='replace') as f:
                    lines = f.readlines()
                
                # 解析包列表（跳过注释行，提取包名和版本）
                packages = []
                for line in lines:
                    line = line.strip()
                    if line and not line.startswith('#') and not line.startswith('-'):
                        # 提取包信息（格式通常是：包名 版本 或其他格式）
                        parts = line.split()
                        if len(parts) >= 1:
                            package_name = parts[0]
                            # 如果是类似 torch==1.0.0 的格式，直接使用
                            if '==' in line or '>=' in line or '<=' in line:
                                packages.append(line)
                            else:
                                # 只有包名，尝试从后续部分获取版本
                                if len(parts) >= 2:
                                    # 假设格式是：包名 版本
                                    version = parts[1]
                                    if version.replace('.', '').isdigit() or version.startswith('v'):
                                        packages.append(f"{package_name}=={version.replace('v', '')}")
                                    else:
                                        packages.append(package_name)
                                else:
                                    packages.append(package_name)
                
                if not packages:
                    self._show_dark_warning("⚠️ 文件为空", 
                                            "环境库列表文件为空或格式错误！", 
                                            f"文件 {env_file} 中没有有效的包信息。\n请确保文件是从'查看环境'功能保存的有效环境库列表文件。")
                    return
                
                # 去重并排序
                packages = sorted(list(set(packages)))
                self._text_enqueue(f"[库列表还原] 从文件读取到 {len(packages)} 个包")
                
            except Exception as e:
                self._show_dark_warning("⚠️ 读取失败", 
                                        "无法读取环境库列表文件！", 
                                        f"读取文件 {env_file} 失败: {e}\n请确保文件格式正确且有读取权限。")
                return
            
            self._text_enqueue(f"[库列表还原] 已读取到 {len(packages)} 个包")
            mirror_url = PYPI_MIRRORS.get(self.mirror_var.get(), '')
            confirm1 = self._show_dark_confirm(
                "⚠️ 第一次确认",
                f"确定要按库列表进行环境对齐吗？\n\n源文件: {os.path.basename(env_file)}\n包数量: {len(packages)}\n\n此操作将比较当前环境与库列表：\n1. 多余的包将被卸载\n2. 版本不一致的包将按列表版本安装\n3. 缺少的包将被安装\n\n是否继续？"
            )
            if not confirm1:
                self._text_enqueue("[库列表还原] 用户在第一次确认时取消")
                return
            confirm2 = self._show_dark_confirm(
                "⚠️ 第二次确认 - 重要警告",
                "⚠️ 重要警告 ⚠️\n\n操作将修改当前Python环境的包集合与版本，可能导致现有环境不可用。\n建议操作前备份当前环境。\n\n第二步确认 - 风险提示：\n\n⚠️ 不可撤销\n⚠️ 将卸载多余包\n⚠️ 将安装指定版本\n\n是否继续？"
            )
            if not confirm2:
                self._text_enqueue("[库列表还原] 用户在第二次确认时取消")
                return
            confirm3 = self._show_dark_confirm(
                "⚠️ 最终确认",
                "这是最终确认！\n\n库列表还原将立即开始，无法撤销。\n\n第三步确认 - 最终确认：\n\n🔴 操作将立即开始\n🔴 无法撤销\n\n确认继续吗？"
            )
            if not confirm3:
                self._text_enqueue("[库列表还原] 用户在最终确认时取消")
                return
            def _run():
                try:
                    self._perform_env_list_restore(packages, env_file, False, True, mirror_url)
                except Exception as e:
                    self._text_enqueue(f"[库列表还原] 运行出错: {e}")
            Thread(target=_run, daemon=True).start()
            
        except Exception as e:
            self._text_enqueue(f"[库列表还原] 启动还原失败: {str(e)}")
            self._enqueue_progress_hide()
    
    def _perform_env_list_restore(self, packages, env_file, upgrade=False, force_reinstall=False, index_url=""):
        """执行库列表还原操作"""
        try:
            self._text_enqueue(f"[库列表还原] 开始对比并按库列表还原环境...")
            self._enqueue_progress_show(0.05)
            python_exe = self.python_exe_path
            desired = {}
            for s in packages:
                t = (s or '').strip()
                if not t:
                    continue
                if '==' in t:
                    name, ver = t.split('==', 1)
                    desired[(name or '').strip().lower()] = (ver or '').strip().lstrip('v')
                else:
                    parts = t.split()
                    if len(parts) >= 2:
                        desired[(parts[0] or '').strip().lower()] = (parts[1] or '').strip().lstrip('v')
                    elif len(parts) == 1:
                        desired[(parts[0] or '').strip().lower()] = ''
            self._text_enqueue(f"[库列表还原] 列表包数量: {len(desired)}")
            res = subprocess.run([python_exe, '-m', 'pip', 'list', '--format=json'], capture_output=True, text=True, timeout=300)
            installed_json = (res.stdout or '').strip() if res.returncode == 0 else '[]'
            try:
                installed_list = json.loads(installed_json)
            except Exception:
                installed_list = []
            installed = {str(x.get('name', '')).strip().lower(): str(x.get('version', '')).strip() for x in installed_list if x.get('name')}
            protected = {'pip', 'setuptools', 'wheel'}
            to_uninstall = [n for n in installed.keys() if n not in desired and n not in protected]
            to_install = []
            matched = 0
            total_check = max(len(desired), 1)
            idx_check = 0
            for n, v in desired.items():
                idx_check += 1
                cur = installed.get(n, '')
                if v:
                    if cur == v:
                        matched += 1
                        self._text_enqueue(f"[库列表还原] 已匹配: {n}=={v}")
                    else:
                        to_install.append(f"{n}=={v}")
                else:
                    if n in installed:
                        matched += 1
                        self._text_enqueue(f"[库列表还原] 已存在: {n}=={cur}")
                    else:
                        to_install.append(n)
                self._enqueue_progress(0.08 + idx_check / total_check * 0.02)
            self._text_enqueue(f"[库列表还原] 需要卸载: {len(to_uninstall)}，需要安装/变更: {len(to_install)}")
            self._enqueue_progress(0.1)
            if to_uninstall:
                total_un = len(to_uninstall)
                for i, name in enumerate(to_uninstall):
                    self._text_enqueue(f"[库列表还原] 卸载 {name} ({i+1}/{total_un})")
                    cmd = [python_exe, '-m', 'pip', 'uninstall', '-y', name]
                    try:
                        subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                    except Exception as e:
                        self._text_enqueue(f"[库列表还原] 卸载出错: {name} - {e}")
                    self._enqueue_progress(0.1 + (i + 1) / max(total_un, 1) * 0.3)
            if to_install:
                base_cmd = [python_exe, '-m', 'pip', 'install']
            if force_reinstall:
                base_cmd.append('--force-reinstall')
            if upgrade:
                base_cmd.append('--upgrade')
            if index_url:
                base_cmd.extend(['--index-url', index_url])
                base_url = index_url.split('//')[-1].split('/')[0]
                base_cmd.extend(['--trusted-host', base_url])
                base_cmd.extend(['--extra-index-url', 'https://pypi.org/simple'])
                base_cmd.extend(['--trusted-host', 'pypi.org'])
            failed_packages = []
            total_in = len(to_install)
            for i, spec in enumerate(to_install):
                self._text_enqueue(f"[库列表还原] 安装 {spec} ({i+1}/{total_in})")
                cmd = base_cmd + [spec]
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
                    if result.returncode != 0:
                        err_text = (result.stderr or '') + '\n' + (result.stdout or '')
                        need_retry = ('No matching distribution found' in err_text) or ('Could not find a version that satisfies' in err_text)
                        if need_retry:
                            self._text_enqueue(f"[库列表还原] 备用源重试 {spec}")
                            fallback_cmd = [python_exe, '-m', 'pip', 'install']
                            if force_reinstall:
                                fallback_cmd.append('--force-reinstall')
                            if upgrade:
                                fallback_cmd.append('--upgrade')
                            fallback_cmd.append(spec)
                            try:
                                retry = subprocess.run(fallback_cmd, capture_output=True, text=True, timeout=1200)
                                if retry.returncode != 0:
                                    failed_packages.append(spec)
                                    msg = (retry.stderr or '').strip().split('\n')[-5:]
                                    for line in msg:
                                        if line.strip() and not line.startswith('  ') and 'WARNING' not in line:
                                            self._text_enqueue(f"[库列表还原] {line.strip()}")
                                else:
                                    self._text_enqueue(f"[库列表还原] 备用源安装成功 {spec}")
                            except subprocess.TimeoutExpired:
                                failed_packages.append(spec)
                                self._text_enqueue(f"[库列表还原] 备用源安装超时，跳过 {spec}")
                            except Exception as e:
                                failed_packages.append(spec)
                                self._text_enqueue(f"[库列表还原] 备用源安装出错: {spec} - {e}")
                        else:
                            failed_packages.append(spec)
                            if result.stderr:
                                errs = result.stderr.strip().split('\n')[-5:]
                                for line in errs:
                                    if line.strip() and not line.startswith('  ') and 'WARNING' not in line:
                                        self._text_enqueue(f"[库列表还原] {line.strip()}")
                except subprocess.TimeoutExpired:
                    failed_packages.append(spec)
                    self._text_enqueue(f"[库列表还原] 安装超时，跳过 {spec}")
                except Exception as e:
                    failed_packages.append(spec)
                    self._text_enqueue(f"[库列表还原] 安装出错: {spec} - {e}")
                self._enqueue_progress(0.4 + (i + 1) / max(total_in, 1) * 0.5)
            if failed_packages:
                self._text_enqueue(f"[库列表还原] 安装失败 {len(failed_packages)} 个")
                save_failed = self._show_dark_confirm("⚠️ 保存失败列表", "是否将安装失败的包列表保存到文件？\n\n保存失败包列表可以帮助您手动处理这些包。\n\n是否保存？")
                if save_failed:
                    self._save_failed_packages(failed_packages, env_file)
            self._text_enqueue("[库列表还原] ✅ 环境已按库列表对齐")
            self._text_enqueue(f"[库列表还原] 📄 源文件: {os.path.basename(env_file)}")
            self._text_enqueue("[库列表还原] 💡 建议重新启动程序以确保所有包正确加载")
        
        except Exception as e:
            self._text_enqueue(f"[库列表还原] 还原过程出错: {str(e)}")
        finally:
            self._enqueue_progress_hide()
    
    def _save_failed_packages(self, failed_packages, source_file):
        """保存安装失败的包列表"""
        try:
            import time
            current_datetime = time.strftime('%Y%m%d_%H%M%S')
            source_name = os.path.splitext(os.path.basename(source_file))[0]
            default_filename = f"{current_datetime}_{source_name}_failed_packages.txt"
            
            file_path = self._ask_saveas_filename_dark(
                title="保存安装失败的包列表",
                filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
                defaultextension=".txt",
                initialfile=default_filename
            )
            
            if file_path:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(f"# 安装失败的包列表\n")
                    f.write(f"# 源文件: {os.path.basename(source_file)}\n")
                    f.write(f"# 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write(f"# 共 {len(failed_packages)} 个包安装失败\n")
                    f.write("#\n")
                    f.write("# ===== 失败包列表 =====\n")
                    for package in failed_packages:
                        f.write(f"{package}\n")
                
                self._text_enqueue(f"[库列表还原] 💾 失败包列表已保存到: {os.path.basename(file_path)}")
                
        except Exception as e:
            self._text_enqueue(f"[库列表还原] ❌ 保存失败包列表时出错: {e}")



    def restore_environment_files(self):
        """还原环境文件（多线程版本，避免UI阻塞，三次确认）"""
        try:
            python_exe = self.python_exe_path
            if not python_exe or not os.path.exists(python_exe):
                self._show_dark_warning("⚠️ Python环境无效", 
                                        "请先设置有效的Python环境路径！", 
                                        "Python环境路径无效或不存在，无法还原环境文件。\n请先选择或浏览有效的Python环境路径。")
                return
            
            python_dir = os.path.dirname(python_exe)
            
            # 让用户选择备份目录（使用自定义文件对话框）
            backup_dir = self._ask_directory_dark("选择要还原的备份目录")
            if not backup_dir:
                self._text_enqueue("[还原] 用户取消选择备份目录")
                return
            
            if not os.path.exists(backup_dir):
                self._show_dark_warning("⚠️ 备份目录无效", 
                                        "选择的备份目录不存在！", 
                                        f"备份目录不存在: {backup_dir}\n请选择有效的备份目录。")
                return
            
            # 检查备份目录是否包含有效的备份内容
            backup_contents = os.listdir(backup_dir)
            if not backup_contents:
                self._show_dark_warning("⚠️ 备份目录为空", 
                                        "选择的备份目录为空！", 
                                        f"备份目录 {backup_dir} 中没有文件。\n请选择包含备份文件的目录。")
                return
            
            # 三次确认机制
            self._text_enqueue(f"[还原] 准备从备份目录还原: {backup_dir}")
            
            # 第一次确认：选择备份目录
            confirm1 = self._show_dark_confirm("⚠️ 第一次确认", 
                                            f"确定要从以下目录还原吗？\n\n备份目录: {backup_dir}\n\n目标目录: {python_dir}\n\n此操作将用备份文件覆盖当前Python环境文件。\n\n第一步确认：\n1. 备份目录: {backup_dir}\n2. 目标目录: {python_dir}\n3. 操作: 覆盖当前环境文件\n\n是否继续？")
            
            if not confirm1:
                self._text_enqueue("[还原] 用户在第一次确认时取消还原操作")
                return
            
            # 第二次确认：风险提示
            confirm2 = self._show_dark_confirm("⚠️ 第二次确认 - 重要警告", 
                                            "⚠️ 重要警告 ⚠️\n\n还原操作将：\n1. 覆盖当前Python环境的所有文件\n2. 可能导致当前环境不可用\n3. 建议在还原前备份当前环境\n\n第二步确认 - 风险提示：\n\n⚠️ 此操作不可撤销！\n⚠️ 将覆盖当前Python环境的所有文件！\n⚠️ 可能导致当前环境配置丢失！\n\n建议操作前备份当前环境。\n\n是否已了解风险并继续？")
            
            if not confirm2:
                self._text_enqueue("[还原] 用户在第二次确认时取消还原操作")
                return
            
            # 第三次确认：最终确认
            confirm3 = self._show_dark_confirm("⚠️ 最终确认", 
                                            "这是最终确认！\n\n还原操作将立即开始，无法撤销。\n\n第三步确认 - 最终确认：\n\n🔴 这是最终确认！\n🔴 操作将立即开始！\n🔴 无法撤销！\n\n确认要进行环境还原吗？")
            
            if not confirm3:
                self._text_enqueue("[还原] 用户在最终确认时取消还原操作")
                return
            
            # 禁用还原按钮，防止重复点击
            if hasattr(self, 'restore_button'):
                self.restore_button.configure(state="disabled")
            
            # 启动后台还原线程
            restore_thread = Thread(target=self._restore_worker_thread, 
                                  args=(backup_dir, python_dir), 
                                  daemon=True)
            restore_thread.start()
            
            # 启动UI更新定时器
            self._start_restore_ui_update()
            
        except Exception as e:
            self._text_enqueue(f"[还原] 启动还原失败: {e}")
            self._restore_restore_ui_state()

    def _restore_worker_thread(self, backup_dir, python_dir):
        """后台还原工作线程 - 使用Windows系统复制命令"""
        try:
            self.restore_status = {
                'backup_dir': backup_dir,
                'python_dir': python_dir,
                'completed': False,
                'error': None
            }
            self._text_enqueue("[还原] 🚀 启动OS极速还原")
            self._text_enqueue(f"[还原] 📁 源目录: {backup_dir}")
            self._text_enqueue(f"[还原] 🎯 目标目录: {python_dir}")
            start_time = time.time()
            success = self._windows_os_copy(backup_dir, python_dir)
            if success and not self._closing:
                elapsed_time = time.time() - start_time
                self._text_enqueue("[还原] ✅ 还原完成！")
                self._text_enqueue(f"[还原] ⏱️ 耗时: {elapsed_time:.1f}秒")
                try:
                    total_size = self._get_directory_size(python_dir)
                    self._text_enqueue(f"[还原] 📦 还原大小: {total_size / (1024**3):.2f} GB")
                except Exception:
                    pass
                self._text_enqueue("[还原] 💡 建议重新启动程序以确保环境配置生效")
            elif self._closing:
                self._text_enqueue("[还原] ⚠️ 还原操作被取消")
            else:
                self._text_enqueue("[还原] ❌ 系统复制失败")
        except Exception as e:
            try:
                self.restore_status['error'] = str(e)
            except Exception:
                pass
            self._text_enqueue(f"[还原] 还原失败: {e}")
        finally:
            try:
                self.restore_status['completed'] = True
            except Exception:
                pass

    def _start_restore_ui_update(self):
        """启动还原UI更新定时器"""
        self.restore_ui_update_id = self.after(100, self._update_restore_ui)

    def _update_restore_ui(self):
        """更新还原UI状态"""
        if hasattr(self, 'restore_status') and self.restore_status.get('completed'):
            # 还原完成，恢复UI状态
            self._restore_restore_ui_state()
            self._enqueue_progress_hide()
            return
        
        # 继续更新
        if hasattr(self, 'restore_ui_update_id'):
            self.restore_ui_update_id = self.after(100, self._update_restore_ui)

    def _restore_restore_ui_state(self):
        """恢复还原UI状态"""
        if hasattr(self, 'restore_button'):
            self.restore_button.configure(state="normal")
        
        # 清理还原状态
        if hasattr(self, 'restore_status'):
            del self.restore_status
        
        if hasattr(self, 'restore_ui_update_id'):
            if self.restore_ui_update_id:
                self.after_cancel(self.restore_ui_update_id)
            del self.restore_ui_update_id

    def find_plugins_with_library(self):
        """查找包含指定库名的插件（弹出模式选择对话框）"""
        try:
            lib_name = self.lib_name_var.get().strip()
            if not lib_name:
                self._show_dark_warning("⚠️ 库名无效", 
                                        "请输入要查找的第三方库名称！", 
                                        "库名输入框为空，无法查找包含该库的插件。\n请在第三方库名称输入框中输入要查找的库名。")
                return
            
            custom_nodes = self.custom_nodes_var.get().strip()
            if not custom_nodes or not os.path.isdir(custom_nodes):
                self._show_dark_warning("⚠️ 目录无效警告", 
                                        f"请先设置有效的CustomNodes目录！\n\n当前路径: {custom_nodes if custom_nodes else '未设置'}", 
                                        "CustomNodes目录无效或不存在，无法查找插件。\n请先选择或浏览有效的CustomNodes目录。")
                return
            
            # 弹出模式选择对话框
            dialog = ctk.CTkToplevel(self)
            dialog.title("选择查找模式")
            dialog.geometry("400x250")
            dialog.transient(self)
            dialog.grab_set()
            
            # 设置暗色标题栏
            self._set_dark_titlebar(dialog)
            
            # 设置对话框居中
            dialog.update_idletasks()
            x = (dialog.winfo_screenwidth() - dialog.winfo_width()) // 2
            y = (dialog.winfo_screenheight() - dialog.winfo_height()) // 2
            dialog.geometry(f"+{x}+{y}")
            
            # 创建对话框内容
            frame = ctk.CTkFrame(dialog)
            frame.pack(fill='both', expand=True, padx=25, pady=25)
            
            ctk.CTkLabel(frame, text="请选择查找模式：", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=10)
            
            # 模式说明
            mode_info = ctk.CTkTextbox(frame, height=80, width=350)
            mode_info.pack(pady=10)
            mode_info.insert('1.0', "精确查找：完全匹配库名（如输入'torch'只匹配'torch'）\n\n模糊查找：部分匹配库名（如输入'torch'可匹配'torch','pytorch','torchvision'等）")
            mode_info.configure(state='disabled')
            
            selected_mode = tk.StringVar()
            
            def on_exact_mode():
                selected_mode.set("exact")
                dialog.destroy()
                self._start_plugin_search(lib_name, custom_nodes, exact=True)
            
            def on_fuzzy_mode():
                selected_mode.set("fuzzy")
                dialog.destroy()
                self._start_plugin_search(lib_name, custom_nodes, exact=False)
            
            def on_cancel():
                dialog.destroy()
                self._text_enqueue("[查找] 用户取消查找操作")
            
            # 按钮区域
            button_frame = ctk.CTkFrame(frame)
            button_frame.pack(pady=(15, 5))
            
            ctk.CTkButton(button_frame, text="精确查找", command=on_exact_mode, width=100).pack(side='left', padx=5)
            ctk.CTkButton(button_frame, text="模糊查找", command=on_fuzzy_mode, width=100).pack(side='left', padx=5)
            ctk.CTkButton(button_frame, text="取消", command=on_cancel, width=80).pack(side='left', padx=5)
            
            # 等待对话框关闭
            dialog.wait_window(dialog)
            
        except Exception as e:
            self._text_enqueue(f"[查找] 启动查找失败: {str(e)}")
            self._enqueue_progress_hide()
    
    def _start_plugin_search(self, lib_name, custom_nodes, exact=False):
        """开始插件查找（内部方法）"""
        mode_name = "精确" if exact else "模糊"
        self._text_enqueue(f"[查找] 开始{mode_name}查找包含库 '{lib_name}' 的插件...")
        self._enqueue_progress_show(0.1)
        
        # 在后端线程中执行查找
        def search_plugins():
            try:
                found_plugins = []
                
                # 只遍历CustomNodes目录下的一层子目录（插件目录）
                for plugin_name in os.listdir(custom_nodes):
                    plugin_path = os.path.join(custom_nodes, plugin_name)
                    if not os.path.isdir(plugin_path):
                        continue
                    
                    # 只检查插件目录下的requirements.txt文件（不递归子目录）
                    req_file = os.path.join(plugin_path, 'requirements.txt')
                    if os.path.exists(req_file):
                        try:
                            with open(req_file, 'r', encoding='utf-8', errors='replace') as f:
                                content = f.read()
                                content_lower = content.lower()
                                lib_name_lower = lib_name.lower()
                                
                                # 根据模式选择匹配方式
                                if exact:
                                    # 精确查找：检查是否作为独立的库名存在
                                    found = False
                                    lines = content.split('\n')
                                    for line in lines:
                                        line = line.strip()
                                        if line and not line.startswith('#'):
                                            # 提取库名（处理各种格式如：torch>=1.0, torch==1.0, torch等）
                                            import re
                                            match = re.match(r'^([a-zA-Z0-9_-]+)', line)
                                            if match:
                                                dep_name = match.group(1).lower()
                                                if dep_name == lib_name_lower:
                                                    found = True
                                                    break
                                else:
                                    # 模糊查找：简单包含匹配
                                    found = lib_name_lower in content_lower
                                
                                if found:
                                    # 提取具体的依赖行
                                    lines = content.split('\n')
                                    matching_lines = []
                                    for line in lines:
                                        line = line.strip()
                                        if line and not line.startswith('#'):
                                            if exact:
                                                # 精确模式：只匹配完全相同的库名
                                                import re
                                                match = re.match(r'^([a-zA-Z0-9_-]+)', line)
                                                if match and match.group(1).lower() == lib_name_lower:
                                                    matching_lines.append(line)
                                            else:
                                                # 模糊模式：包含即可
                                                if lib_name_lower in line.lower():
                                                    matching_lines.append(line)
                                    
                                    if matching_lines:
                                        found_plugins.append({
                                            'plugin': plugin_name,
                                            'file': 'requirements.txt',
                                            'dependencies': matching_lines
                                        })
                        except Exception as e:
                            self._ui_queue.put(('text', f"[查找] 读取文件 {req_file} 失败: {e}"))
                
                # 显示结果
                if found_plugins:
                    self._ui_queue.put(('text', f"[{mode_name}查找] 找到 {len(found_plugins)} 个插件包含库 '{lib_name}':"))
                    
                    for plugin_info in found_plugins:
                        plugin_name = plugin_info['plugin']
                        req_file = plugin_info['file']
                        deps = plugin_info['dependencies']
                        
                        self._ui_queue.put(('text', f"\n📁 {plugin_name}/"))
                        self._ui_queue.put(('text', f"   文件: {req_file}"))
                        self._ui_queue.put(('text', f"   相关依赖:"))
                        for dep in deps:
                            self._ui_queue.put(('text', f"     - {dep}"))
                else:
                    if exact:
                        self._ui_queue.put(('text', f"[{mode_name}查找] 未找到包含库 '{lib_name}' 的插件"))
                        self._ui_queue.put(('text', f"提示：可以尝试使用模糊查找模式搜索类似的库名"))
                    else:
                        self._ui_queue.put(('text', f"[{mode_name}查找] 未找到包含 '{lib_name}' 相关内容的插件"))
                        self._ui_queue.put(('text', f"提示：可以尝试使用精确查找模式搜索特定的库名"))
                
            except Exception as e:
                self._ui_queue.put(('text', f"[{mode_name}查找] 查找过程出错: {str(e)}"))
            finally:
                self._ui_queue.put(('progress_hide', None))
        
        Thread(target=search_plugins, daemon=True).start()

    def refresh_git_plugin_list(self):
        """手动刷新git插件列表"""
        try:
            custom_nodes = self.custom_nodes_var.get().strip()
            if not custom_nodes or not os.path.isdir(custom_nodes):
                self._show_dark_warning("⚠️ 目录无效警告", 
                                        f"请先设置有效的CustomNodes目录！\n\n当前路径: {custom_nodes if custom_nodes else '未设置'}", 
                                        "CustomNodes目录无效或不存在，无法扫描插件。\n请先选择或浏览有效的CustomNodes目录。")
                return
            
            self._text_enqueue("[刷新] 开始刷新git插件列表...")
            self._scan_git_plugins(custom_nodes)
            self._text_enqueue("[刷新] git插件列表刷新完成")
            
        except Exception as e:
            self._text_enqueue(f"[刷新] 刷新插件列表失败: {e}")

    def _scan_git_plugins(self, custom_nodes_dir):
        """扫描CustomNodes目录中的git插件，自动添加到插件历史"""
        try:
            import subprocess
            
            self._text_enqueue("[扫描] 开始扫描git插件...")
            found_plugins = []
            
            # 遍历CustomNodes目录下的所有子目录
            if not os.path.isdir(custom_nodes_dir):
                return
            
            for item in os.listdir(custom_nodes_dir):
                item_path = os.path.join(custom_nodes_dir, item)
                if not os.path.isdir(item_path):
                    continue
                
                # 检查是否为git仓库
                git_config_path = os.path.join(item_path, '.git', 'config')
                if not os.path.exists(git_config_path):
                    continue
                
                # 尝试获取远程仓库URL
                try:
                    result = subprocess.run(
                        ['git', 'remote', 'get-url', 'origin'],
                        cwd=item_path,
                        capture_output=True,
                        text=True,
                        errors='replace',
                        timeout=10
                    )
                    
                    if result.returncode == 0:
                        remote_url = (result.stdout or '').strip()
                        if remote_url and remote_url not in self.plugin_history:
                            found_plugins.append(remote_url)
                            self._text_enqueue(f"[扫描] 发现git插件: {item} -> {remote_url}")
                
                except Exception as e:
                    self._text_enqueue(f"[扫描] 获取 {item} 的git信息失败: {e}")
            
            # 将发现的插件添加到历史记录
            if found_plugins:
                for plugin_url in found_plugins:
                    self._add_to_plugin_history(plugin_url)
                self._text_enqueue(f"[扫描] 共发现 {len(found_plugins)} 个git插件，已添加到列表")
            else:
                self._text_enqueue("[扫描] 未发现git插件")
                
        except Exception as e:
            self._text_enqueue(f"[扫描] 扫描git插件失败: {e}")

    def _set_dark_titlebar(self, window):
        """设置Windows暗色标题栏"""
        if sys.platform.startswith("win"):
            try:
                # 获取窗口句柄
                hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
                
                # 设置暗色模式
                DWMWA_USE_IMMERSIVE_DARK_MODE = 20
                DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1 = 19
                
                # 尝试新的API (Windows 10 20H1+)
                value = ctypes.c_int(1)  # 1 = 暗色模式
                result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 
                    DWMWA_USE_IMMERSIVE_DARK_MODE, 
                    ctypes.byref(value), 
                    ctypes.sizeof(value)
                )
                
                # 如果新的API失败，尝试旧的API (Windows 10 1903+)
                if result != 0:
                    result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, 
                        DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1, 
                        ctypes.byref(value), 
                        ctypes.sizeof(value)
                    )
                    
                # 如果仍然失败，可能是系统不支持，记录调试信息
                if result != 0:
                    print(f"[调试] 无法设置暗色标题栏，错误码: {result}")
                    
            except Exception as e:
                print(f"[调试] 设置暗色标题栏失败: {e}")
                pass  # 如果设置失败，不影响程序运行

    def _show_dark_warning(self, title, message, details=None):
        """显示暗色调的警告对话框"""
        dialog = ctk.CTkToplevel(self)
        dialog.title(title)
        
        # 计算对话框大小，如果有详细信息则增大尺寸
        if details:
            dialog.geometry("500x350")
        else:
            dialog.geometry("400x200")
            
        dialog.transient(self)
        dialog.grab_set()
        
        # 设置暗色标题栏
        self._set_dark_titlebar(dialog)
        
        # 居中显示
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - dialog.winfo_width()) // 2
        y = (dialog.winfo_screenheight() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")
        
        # 主容器
        main_frame = ctk.CTkFrame(dialog)
        main_frame.pack(fill='both', expand=True, padx=20, pady=20)
        
        # 警告图标和标题
        icon_frame = ctk.CTkFrame(main_frame)
        icon_frame.pack(fill='x', pady=(0, 15))
        ctk.CTkLabel(icon_frame, text="⚠️", font=ctk.CTkFont(size=24)).pack(side='left', padx=(0, 10))
        ctk.CTkLabel(icon_frame, text=title, font=ctk.CTkFont(size=16, weight="bold")).pack(side='left')
        
        # 消息文本
        ctk.CTkLabel(main_frame, text=message, text_color="white", justify="left", 
                    font=ctk.CTkFont(size=12)).pack(pady=8, padx=10, anchor='w')
        
        # 详细信息（如果有）
        if details:
            details_frame = ctk.CTkFrame(main_frame, fg_color="gray20")
            details_frame.pack(fill='both', expand=True, padx=10, pady=10)
            
            # 创建可滚动的文本框
            text_box = ctk.CTkTextbox(details_frame, height=80, font=ctk.CTkFont(size=10))
            text_box.pack(fill='both', expand=True, padx=10, pady=10)
            text_box.insert('1.0', details)
            text_box.configure(state='disabled')
        
        # 按钮
        button_frame = ctk.CTkFrame(main_frame)
        button_frame.pack(pady=(15, 0))
        
        def on_ok():
            dialog.destroy()
        
        ctk.CTkButton(button_frame, text="确定", command=on_ok, width=100, 
                     font=ctk.CTkFont(size=12)).pack()
        
        # 等待对话框关闭
        self.wait_window(dialog)





    def _show_dark_error(self, title, message, details=None):
        """显示暗色调的错误对话框"""
        dialog = ctk.CTkToplevel(self)
        dialog.title(title)
        
        # 计算对话框大小，如果有详细信息则增大尺寸
        if details:
            dialog.geometry("500x350")
        else:
            dialog.geometry("400x200")
            
        dialog.transient(self)
        dialog.grab_set()
        
        # 设置暗色标题栏
        self._set_dark_titlebar(dialog)
        
        # 居中显示
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - dialog.winfo_width()) // 2
        y = (dialog.winfo_screenheight() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")
        
        # 主容器
        main_frame = ctk.CTkFrame(dialog)
        main_frame.pack(fill='both', expand=True, padx=20, pady=20)
        
        # 错误图标和标题
        icon_frame = ctk.CTkFrame(main_frame)
        icon_frame.pack(fill='x', pady=(0, 15))
        ctk.CTkLabel(icon_frame, text="❌", font=ctk.CTkFont(size=24)).pack(side='left', padx=(0, 10))
        ctk.CTkLabel(icon_frame, text=title, font=ctk.CTkFont(size=16, weight="bold")).pack(side='left')
        
        # 消息文本
        ctk.CTkLabel(main_frame, text=message, text_color="#ff6b6b", justify="left", 
                    font=ctk.CTkFont(size=12)).pack(pady=8, padx=10, anchor='w')
        
        # 详细信息（如果有）
        if details:
            details_frame = ctk.CTkFrame(main_frame, fg_color="gray20")
            details_frame.pack(fill='both', expand=True, padx=10, pady=10)
            
            # 创建可滚动的文本框
            text_box = ctk.CTkTextbox(details_frame, height=80, font=ctk.CTkFont(size=10))
            text_box.pack(fill='both', expand=True, padx=10, pady=10)
            text_box.insert('1.0', details)
            text_box.configure(state='disabled')
        
        # 按钮
        button_frame = ctk.CTkFrame(main_frame)
        button_frame.pack(pady=(15, 0))
        
        def on_ok():
            dialog.destroy()
        
        ctk.CTkButton(button_frame, text="确定", command=on_ok, width=100, 
                     font=ctk.CTkFont(size=12)).pack()
        
        # 等待对话框关闭
        self.wait_window(dialog)

    def _show_dark_confirm(self, title, message):
        dialog = ctk.CTkToplevel(self)
        dialog.title(title)
        dialog.geometry("620x500")  # 增加高度
        dialog.transient(self)
        dialog.grab_set()
        
        # 设置暗色标题栏
        self._set_dark_titlebar(dialog)
        
        # 居中显示
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - dialog.winfo_width()) // 2
        y = (dialog.winfo_screenheight() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")
        
        # 结果变量
        result = False
        
        # 主容器
        main_frame = ctk.CTkFrame(dialog)
        main_frame.pack(fill='both', expand=True, padx=20, pady=20)
        
        icon_frame = ctk.CTkFrame(main_frame)
        icon_frame.pack(fill='x', pady=(0, 15))
        ctk.CTkLabel(icon_frame, text="❓", font=ctk.CTkFont(size=24)).pack(side='left', padx=(0, 10))
        ctk.CTkLabel(icon_frame, text=title, font=ctk.CTkFont(size=16, weight="bold")).pack(side='left')
        msg_box = ctk.CTkTextbox(main_frame, font=ctk.CTkFont(size=12))
        msg_box.pack(fill='both', expand=True, padx=10, pady=10)
        msg_box.insert('1.0', message)
        msg_box.configure(state='disabled')
        
        # 按钮
        button_frame = ctk.CTkFrame(main_frame)
        button_frame.pack(pady=(15, 0))
        
        def on_yes():
            nonlocal result
            result = True
            dialog.destroy()
        
        def on_no():
            nonlocal result
            result = False
            dialog.destroy()
        
        ctk.CTkButton(button_frame, text="是", command=on_yes, width=80, 
                     font=ctk.CTkFont(size=12)).pack(side='left', padx=(0, 10))
        ctk.CTkButton(button_frame, text="否", command=on_no, width=80, 
                     font=ctk.CTkFont(size=12)).pack(side='left')
        
        # 等待对话框关闭
        self.wait_window(dialog)
        return result

    def _show_dark_info(self, title, message, details=None):
        dialog = ctk.CTkToplevel(self)
        dialog.title(title)
        
        # 更大的默认尺寸，便于长文本显示
        if details:
            dialog.geometry("700x480")
        else:
            dialog.geometry("500x280")
            
        dialog.transient(self)
        dialog.grab_set()
        
        # 设置暗色标题栏
        self._set_dark_titlebar(dialog)
        
        # 居中显示
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - dialog.winfo_width()) // 2
        y = (dialog.winfo_screenheight() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")
        
        # 主容器
        main_frame = ctk.CTkFrame(dialog)
        main_frame.pack(fill='both', expand=True, padx=20, pady=20)
        
        # 信息图标和标题
        icon_frame = ctk.CTkFrame(main_frame)
        icon_frame.pack(fill='x', pady=(0, 15))
        ctk.CTkLabel(icon_frame, text="ℹ️", font=ctk.CTkFont(size=24)).pack(side='left', padx=(0, 10))
        ctk.CTkLabel(icon_frame, text=title, font=ctk.CTkFont(size=16, weight="bold")).pack(side='left')
        
        # 消息文本
        ctk.CTkLabel(main_frame, text=message, text_color="white", justify="left", 
                    font=ctk.CTkFont(size=12)).pack(pady=8, padx=10, anchor='w')
        
        # 详细信息（如果有）
        if details:
            details_frame = ctk.CTkFrame(main_frame, fg_color="gray20")
            details_frame.pack(fill='both', expand=True, padx=10, pady=10)
            
            # 创建可滚动的文本框（加大高度）
            text_box = ctk.CTkTextbox(details_frame, height=220, font=ctk.CTkFont(size=11))
            text_box.pack(fill='both', expand=True, padx=10, pady=10)
            text_box.insert('1.0', details)
            text_box.configure(state='disabled')
        
        # 按钮
        button_frame = ctk.CTkFrame(main_frame)
        button_frame.pack(pady=(15, 0))
        
        def on_ok():
            dialog.destroy()
        
        ctk.CTkButton(button_frame, text="确定", command=on_ok, width=100, 
                     font=ctk.CTkFont(size=12)).pack()
        
        # 等待对话框关闭
        self.wait_window(dialog)

    def _show_dark_input_dialog(self, title, prompt):
        """显示暗色调的输入对话框"""
        dialog = ctk.CTkToplevel(self)
        dialog.title(title)
        dialog.geometry("400x260")
        dialog.transient(self)
        dialog.grab_set()
        
        # 设置暗色标题栏
        self._set_dark_titlebar(dialog)
        
        # 居中显示
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - dialog.winfo_width()) // 2
        y = (dialog.winfo_screenheight() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{x}+{y}")
        
        # 主容器
        main_frame = ctk.CTkFrame(dialog)
        main_frame.pack(fill='both', expand=True, padx=20, pady=20)
        
        # 标题
        ctk.CTkLabel(main_frame, text=title, font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(0, 15))
        
        # 提示文本
        ctk.CTkLabel(main_frame, text=prompt, text_color="white", justify="left", 
                    font=ctk.CTkFont(size=12)).pack(pady=8, padx=10)
        
        # 输入框
        input_var = ctk.StringVar()
        input_entry = ctk.CTkEntry(main_frame, textvariable=input_var, width=300)
        input_entry.pack(pady=8)
        
        # 按钮区域
        button_frame = ctk.CTkFrame(main_frame)
        button_frame.pack(pady=15)
        
        result = {"value": None, "cancelled": True}
        
        def on_ok():
            result["value"] = input_var.get()
            result["cancelled"] = False
            dialog.destroy()
        
        def on_cancel():
            result["cancelled"] = True
            dialog.destroy()
        
        ctk.CTkButton(button_frame, text="确定", command=on_ok, width=100, 
                     font=ctk.CTkFont(size=12)).pack(side='left', padx=5)
        ctk.CTkButton(button_frame, text="取消", command=on_cancel, width=100, 
                     font=ctk.CTkFont(size=12)).pack(side='left', padx=5)
        
        # 绑定回车键
        input_entry.bind('<Return>', lambda e: on_ok())
        input_entry.focus()
        
        # 等待对话框关闭
        self.wait_window(dialog)
        
        return None if result["cancelled"] else result["value"]

    def _add_to_cmd_history(self, cmd: str):
        """将命令添加到历史记录中"""
        try:
            if not cmd:
                return
            # 去重，最近使用排前
            self.cmd_history = [c for c in self.cmd_history if c != cmd]
            self.cmd_history.insert(0, cmd)
            try:
                self.cmd_cb.configure(values=self.cmd_history)
            except Exception:
                pass
            self.save_config()
        except Exception:
            pass

    # ---------------- 事件处理 ----------------
    def on_python_env_change(self, _=None):
        self.python_exe_path = self.python_env_var.get()
        self.update_result_text(f"已切换Python环境: {self.python_exe_path}")
        self.save_config()  # 切换环境时立即保存

    def on_mirror_change(self, _=None):
        # 切换镜像源时立即保存到配置
        self.selected_mirror = self.mirror_var.get()
        mirror_url = PYPI_MIRRORS.get(self.selected_mirror, '')
        self.update_result_text(f"已切换到镜像源: {self.selected_mirror}{' (' + mirror_url + ')' if mirror_url else ''}")
        self.save_config()

    def select_python_environment(self):
        path = self._ask_open_filename_dark(title="选择python.exe", filetypes=[("Python Executable", "python*.exe"), ("All files", "*.*")])
        if path:
            if path not in self.python_paths:
                self.python_paths.append(path)
                self.python_env_cb.configure(values=self.python_paths)
            self.python_exe_path = path
            self.python_env_var.set(path)
            self.save_config()
            self.update_result_text(f"已添加Python环境: {path}")

    def delete_python_environment(self):
        if not self.python_exe_path:
            self._show_dark_warning("⚠️ 输入验证", "请先选择Python环境")
            return
        if self._show_dark_confirm("确认删除", f"确定删除环境 {self.python_exe_path}?"):
            if self.python_exe_path in self.python_paths:
                self.python_paths.remove(self.python_exe_path)
            self.python_env_cb.configure(values=self.python_paths)
            self.python_exe_path = self.python_paths[0] if self.python_paths else ""
            self.python_env_var.set(self.python_exe_path)
            self.save_config()
            self.update_result_text("已删除环境")

    # ---------------- 镜像测速逻辑 ----------------
    def _on_mirror_dropdown_click(self, _=None):
        # 点击下拉框时触发一次测速（子线程），并自动应用最快国内源
        if not self.python_exe_path:
            self._show_dark_warning("⚠️ 输入验证", "未选择Python环境，请先选择一个有效的Python环境")
            return
        try:
            self.progress_bar.pack(fill='x', pady=(8, 0))
        except Exception:
            pass
        self.progress_bar.set(0)
        self.update_result_text("开始测试国内镜像源速度...\n")
        t = Thread(target=self._perform_mirror_test, daemon=True)
        t.start()

    def _test_url_connectivity(self, url: str, timeout: float = 5.0) -> float | None:
        # 使用 HEAD 请求测试连通性与响应时间；忽略证书以提高兼容性
        import urllib.request, ssl, time
        start = time.time()
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(url, method='HEAD')
            with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
                if resp.status < 400:
                    return time.time() - start
                return None
        except Exception:
            return None

    def _perform_mirror_test(self):
        try:
            # 仅测试国内源
            mirrors = [(name, url) for name, url in PYPI_MIRRORS.items() if url]
            total = len(mirrors)
            results: list[tuple[str, float]] = []
            for idx, (name, url) in enumerate(mirrors):
                # 1) 先做HTTP HEAD连通性测试
                cost = self._test_url_connectivity(url)
                if cost is not None:
                    results.append((name, cost))
                    self.update_result_text(f"[镜像测速] {name}: {cost:.2f}s (HTTP)")
                else:
                    # 2) 回退到pip --dry-run 测试
                    try:
                        import time as _t
                        start = _t.time()
                        host = url.split('/')[2]
                        cmd = [self.python_exe_path or 'python', '-m', 'pip', 'install', '--dry-run', 'pip', '--index-url', url, '--trusted-host', host]
                        proc = __import__('subprocess').run(cmd, stdout=__import__('subprocess').PIPE, stderr=__import__('subprocess').STDOUT, text=True, errors='replace', timeout=20)
                        elapsed = _t.time() - start
                        if proc.returncode == 0:
                            results.append((name, elapsed))
                            self.update_result_text(f"[镜像测速] {name}: {elapsed:.2f}s (pip)")
                        else:
                            out = (proc.stdout or '').strip().splitlines()
                            self.update_result_text(f"[镜像测速] {name}: 失败 (pip返回码{proc.returncode}) {out[:1]}")
                    except Exception as e:
                        self.update_result_text(f"[镜像测速] {name}: 异常 {e}")
                # 更新进度条
                try:
                    self.after(0, lambda v=(idx + 1) / max(1, total): self.progress_bar.set(v))
                except Exception:
                    pass

            if results:
                results.sort(key=lambda x: x[1])
                fastest_mirror = results[0][0]
                self.update_result_text(f"\n已自动选择最快的镜像源: {fastest_mirror}")
                self.after(0, lambda: (self.mirror_var.set(fastest_mirror), self.on_mirror_change()))
            else:
                self.update_result_text("\n测速未找到可用镜像，已保留当前选择")
        except Exception as e:
            self.update_result_text(f"镜像测速异常: {e}")
        finally:
            try:
                self.after(0, self.progress_bar.pack_forget)
            except Exception:
                pass

    # ---------------- 插件维护逻辑 ----------------
    def _paths_share_first_two_levels(self, p1: str, p2: str) -> bool:
        try:
            if not p1 or not p2:
                return False
            d1 = os.path.abspath(os.path.dirname(p1))
            d2 = os.path.abspath(p2)
            parts1 = [x for x in d1.replace('\\', '/').split('/') if x]
            parts2 = [x for x in d2.replace('\\', '/').split('/') if x]
            # Windows驱动器号不计入层级对比
            if len(parts1) > 0 and ':' in parts1[0]:
                parts1 = parts1[1:]
            if len(parts2) > 0 and ':' in parts2[0]:
                parts2 = parts2[1:]
            return len(parts1) >= 1 and len(parts2) >= 1 and parts1[0] == parts2[0]
        except Exception:
            return False

    def _same_environment_root(self, python_exe: str, plugin_dir: str) -> bool:
        """按用户规则：盘符+一级目录相同即视为同一环境，例如 F:/kontext/ ..."""
        try:
            if not python_exe or not plugin_dir:
                return False
            # 使用 python.exe 的实际路径与插件目录进行盘符+一级目录匹配（大小写不敏感）
            py_abs = os.path.abspath(python_exe)
            plug_abs = os.path.abspath(plugin_dir)
            d_py, p_py = os.path.splitdrive(py_abs)
            d_plug, p_plug = os.path.splitdrive(plug_abs)
            d_py = (d_py or '').lower(); d_plug = (d_plug or '').lower()
            parts_py = [x.lower() for x in p_py.replace('\\', '/').split('/') if x]
            parts_plug = [x.lower() for x in p_plug.replace('\\', '/').split('/') if x]
            if not parts_py or not parts_plug:
                return False
            return (d_py == d_plug) and (parts_py[0] == parts_plug[0])
        except Exception:
            return False

    def add_customnodes_dir(self):
        # 浏览选择目录 → 更新历史 → 列出依赖文件到下拉框
        # 以Python环境的上级目录作为初始化默认路径
        starting_dir = self._get_python_parent_dir()
        path = self._ask_directory_dark(title="选择CustomNodes目录", starting_dir=starting_dir)
        if not path:
            return
        self.custom_nodes_var.set(path)
        self._add_to_custom_nodes_history(path)
        if not os.path.isdir(path):
            self._show_dark_warning("⚠️ 路径验证", "选择的CustomNodes目录不存在")
            return
        self.save_config()
        # 若该目录已有上次检测结果，直接恢复 missing 列表；否则仅罗列文件
        if path in getattr(self, '_missing_cache', {}):
            restored = self._missing_cache[path]
            self._enqueue_deps_values(restored)
        else:
            self._list_dependency_files(path)  # 首次进入：仅罗列文件

    def _scan_customnodes_async(self, dir_path: str):
        try:
            # 传入进度回调，让进度条实时走动
            def _progress(p: float):
                 # 立即在主线程刷新进度条，0~1 -> 0~100
                 self._ui_queue.put(('progress', p))
            res = self.tools.scan_customnodes_dependencies(dir_path, self.python_exe_path,
                                                         list(getattr(self, 'requirements_cache', set())),
                                                         progress_cb=_progress)
            missing_files = res.get('missing_files', [])
            all_ok_files = res.get('all_ok_files', [])
            missing_packages = res.get('missing_packages', [])
            msg = res.get('message', '')
            # 将文本与依赖列表更新请求入队，交由主线程刷新
            if msg:
                self._enqueue_text(msg)
            
            # 显示未安装的第三方库（而不是文件路径）
            if missing_packages:
                lines = "\n".join([f"  - {pkg}" for pkg in missing_packages])
                self._enqueue_text(f"[插件维护] 未安装的第三方库 ({len(missing_packages)}个)：")
                self._enqueue_text(lines)
            elif missing_files:
                self._enqueue_text(f"[插件维护] 发现 {len(missing_files)} 个依赖文件需要安装")
            elif all_ok_files:
                self._enqueue_text("[插件维护] 所有依赖均已安装")
            else:
                self._enqueue_text("[插件维护] 未找到依赖文件")
            # 更新依赖下拉（仅未完全安装）
            self._enqueue_deps_values(missing_files)
            # 缓存：以插件目录为 key 存【未完全安装】列表
            if not hasattr(self, '_missing_cache'):
                self._missing_cache = {}   # dict[插件目录] -> list(绝对路径)
            self._missing_cache[dir_path] = missing_files
            self.save_config()
            # 补充扫描统计到结果框（主线程）
            if missing_files:
                self._enqueue_text(f"[插件维护] 未安装依赖的文件: {len(missing_files)} 个")
            else:
                self._enqueue_text("[插件维护] 所有依赖均已安装，无需处理。")
        except Exception as e:
            self._enqueue_text(f"[插件维护] 扫描失败: {e}")

    def clone_plugin_into_customnodes(self):
        url = self.git_url_var.get().strip()
        dest = self.custom_nodes_var.get().strip()
        if not url:
            self._show_dark_warning("⚠️ Git地址警告", "请输入Git插件地址！", 
                                   "Git插件地址输入框为空，无法进行克隆操作。\n请在Git Clone插件地址输入框中输入有效的Git仓库地址。")
            return
        if not dest or not os.path.isdir(dest):
            self._show_dark_warning("⚠️ 目录无效警告", 
                                    f"请先设置有效的CustomNodes目录！\n\n当前路径: {dest if dest else '未设置'}", 
                                    "CustomNodes目录无效或不存在，无法作为克隆目标。\n请先选择或浏览有效的CustomNodes目录。")
            return
        self._add_to_plugin_history(url)   # 立即追加历史
        Thread(target=self._clone_plugin_async, args=(url, dest), daemon=True).start()

    def _clone_plugin_async(self, url: str, dest_dir: str, max_retry: int = 1):
        import shutil, subprocess
        plugin_name = url.rstrip("/").split("/")[-1].replace(".git", "")
        target = os.path.join(dest_dir, plugin_name)

        # 仅一次克隆，不做自动删除/重试；依赖安装由用户手动操作
        self._text_enqueue(f"[克隆] 开始：{plugin_name}")
        # ---- 检测 git 命令 ----
        try:
            subprocess.run(["git", "--version"], capture_output=True, text=True, errors='replace', check=True)
        except Exception:
            self._text_enqueue("[克隆] 错误：未找到 git 命令，请安装 Git 并置于 PATH")
            return

        # ---- git clone（若已存在则跳过克隆并提示） ----
        res = self.tools.git_clone(url, dest_dir)
        self._text_enqueue(res.get("message", ""))
        if not res.get("ok"):
            return
        
        # 克隆成功后，立即更新插件历史并刷新列表框
        if res.get("ok"):
            self._text_enqueue(f"[克隆] {plugin_name} 克隆成功，已添加到插件列表")
            # 确保URL在历史记录中（_add_to_plugin_history已在主线程调用，这里再次确认）
            self._add_to_plugin_history(url)

        # ---- 扫描依赖文件，仅加入列表并显示内容，不做自动安装 ----
        self._text_enqueue("[克隆] 开始检测新插件依赖...")
        # 显示进度条（主线程处理），并初始一点进度
        self._enqueue_progress_show(0.05)
        scan_res = self.tools.scan_customnodes_dependencies(target, self.python_exe_path, [], progress_cb=lambda p: self._progress_enqueue(p))
        missing_files = scan_res.get("missing_files", []) or []
        ok_files = scan_res.get("all_ok_files", []) or []
        missing_packages = scan_res.get("missing_packages", []) or []
        all_files = sorted(set(missing_files + ok_files))
        
        # 显示未安装的第三方库（主要信息）
        if missing_packages:
            pkg_lines = "\n".join([f"  - {pkg}" for pkg in missing_packages])
            self._text_enqueue(f"[克隆] 发现未安装的第三方库 ({len(missing_packages)}个)：")
            self._text_enqueue(pkg_lines)
        elif missing_files:
            self._text_enqueue(f"[克隆] 发现 {len(missing_files)} 个依赖文件需要安装")
        else:
            self._text_enqueue("[克隆] 所有依赖均已安装")
        
        if all_files:
            # 更新依赖列表并设定当前值为首个文件或优先 requirements.txt
            prefer = self.tools.find_dependency_file(target) or all_files[0]
            try:
                # 以“追加模式”加入列表，不覆盖现有值
                self._enqueue_deps_values_append(all_files)
                # 选择优先文件为当前项
                self._enqueue_deps_select(prefer)
            except Exception:
                pass
            # 显示选定依赖文件内容在右侧（次要信息）
            try:
                with open(prefer, 'r', encoding='utf-8', errors='replace') as f:
                    content = f.read()
                self._text_enqueue(f"\n===== {os.path.basename(prefer)} 文件内容 =====")
                self._text_enqueue(content)
            except Exception as e:
                self._text_enqueue(f"读取依赖文件失败: {e}")
            self._text_enqueue("[克隆] 依赖文件已加入下拉列表，安装请手动执行相关功能")
        else:
            self._text_enqueue("[克隆] 未发现依赖文件")
        # 隐藏进度条
        self._enqueue_progress_hide()
        # 保存配置（历史等）
        try:
            self.save_config()
        except Exception:
            pass

    def check_plugin_updates(self):
        """检查插件更新 - 检查插件地址列表中的插件是否有更新"""
        try:
            custom_nodes = self.custom_nodes_var.get().strip()
            if not custom_nodes or not os.path.isdir(custom_nodes):
                self._show_dark_warning("⚠️ 目录无效警告", 
                                        f"请先设置有效的CustomNodes目录！\n\n当前路径: {custom_nodes if custom_nodes else '未设置'}", 
                                        "CustomNodes目录无效或不存在，无法检查插件更新。\n请先选择或浏览有效的CustomNodes目录。")
                return
            
            self._text_enqueue("[检查更新] 开始检查插件更新...")
            self._enqueue_progress_show(0.1)
            
            # 如果插件历史为空，自动扫描git插件
            if not self.plugin_history or all(not url for url in self.plugin_history):
                self._text_enqueue("[检查更新] 插件历史为空，开始扫描git插件...")
                self._scan_git_plugins(custom_nodes)
            
            # 获取插件历史中的地址对应的目录
            plugin_dirs = []
            for url in self.plugin_history:
                if not url:
                    continue
                # 从URL推断目录名
                repo_name = url.rstrip('/').split('/')[-1]
                if repo_name.endswith('.git'):
                    repo_name = repo_name[:-4]
                plugin_dir = os.path.join(custom_nodes, repo_name)
                if os.path.isdir(plugin_dir):
                    plugin_dirs.append(plugin_dir)
            
            if not plugin_dirs:
                self._text_enqueue("[检查更新] 没有找到可检查的插件目录")
                self._enqueue_progress_hide()
                return
            
            self._text_enqueue(f"[检查更新] 找到 {len(plugin_dirs)} 个插件目录，开始检查...")
            
            # 在后端线程中执行检查
            def check_updates():
                try:
                    result = self.tools.git_check_updates(plugin_dirs)
                    updates = result.get('updates', [])
                    message = result.get('message', '')
                    
                    # 将结果加入队列，让主线程显示
                    self._ui_queue.put(('text', f"[检查更新] {message}"))
                    
                    if updates:
                        has_updates_count = 0
                        for update in updates:
                            path = update.get('path', '')
                            has_update = update.get('has_update', False)
                            current_commit = update.get('current_commit', '')
                            latest_commit = update.get('latest_commit', '')
                            msg = update.get('message', '')
                            
                            plugin_name = os.path.basename(path)
                            status = "有更新" if has_update else "已是最新"
                            
                            if has_update:
                                has_updates_count += 1
                                update_info = f"  - {plugin_name}: {status}"
                                if current_commit and latest_commit:
                                    update_info += f" ({current_commit} -> {latest_commit})"
                                self._ui_queue.put(('text', update_info))
                            else:
                                self._ui_queue.put(('text', f"  - {plugin_name}: {status}"))
                        
                        if has_updates_count > 0:
                            self._ui_queue.put(('text', f"\n[检查更新] 共有 {has_updates_count} 个插件需要更新"))
                            self._ui_queue.put(('text', "可以点击'克隆安装'按钮来更新有变化的插件"))
                        else:
                            self._ui_queue.put(('text', "\n[检查更新] 所有插件都是最新版本"))
                    
                except Exception as e:
                    self._ui_queue.put(('text', f"[检查更新] 检查过程出错: {str(e)}"))
                finally:
                    self._ui_queue.put(('progress_hide', None))
            
            Thread(target=check_updates, daemon=True).start()
            
        except Exception as e:
            self._text_enqueue(f"[检查更新] 启动检查失败: {str(e)}")
            self._enqueue_progress_hide()

    def _add_to_plugin_history(self, url: str):
        try:
            if not url:
                return
            # 去重，最近使用排前
            self.plugin_history = [u for u in self.plugin_history if u != url]
            self.plugin_history.insert(0, url)
            try:
                # 保持空行在开头
                display_history = [''] + self.plugin_history
                self.git_url_cb.configure(values=display_history)
            except Exception:
                pass
            self.save_config()
        except Exception:
            pass

    def _add_to_custom_nodes_history(self, path: str):
        try:
            if not path:
                return
            self.custom_nodes_history = [p for p in self.custom_nodes_history if p != path]
            self.custom_nodes_history.insert(0, path)
            try:
                self.custom_nodes_cb.configure(values=self.custom_nodes_history)
            except Exception:
                pass
            self.save_config()
        except Exception:
            pass

    def on_custom_nodes_change(self, _=None):
        # 下拉选择目录 → 更新历史 → 列出依赖文件到下拉框
        path = self.custom_nodes_var.get().strip()
        if not path:
            return
        self._add_to_custom_nodes_history(path)
        self.save_config()
        # 若该目录已有上次检测结果，直接恢复 missing 列表；否则仅罗列文件
        if path in getattr(self, '_missing_cache', {}):
            restored = self._missing_cache[path]
            self._enqueue_deps_values(restored)
        else:
            self._list_dependency_files(path)  # 首次进入：仅罗列文件

    def on_deps_file_selected(self, _=None):
        """下拉选择单个依赖文件时，立即把文件内容显示到结果框"""
        path = self.deps_list_var.get()
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            self._enqueue_text(f"===== {os.path.basename(path)} =====")
            self._enqueue_text(content)
        except Exception as e:
            self._enqueue_text(f"读取依赖文件失败: {e}")

    def manual_add_requirements(self):
        """手动浏览requirements.txt文件并追加到依赖列表"""
        file_path = self._ask_open_filename_dark(
            title="选择requirements.txt文件",
            filetypes=[("Requirements文件", "requirements*.txt"), ("文本文件", "*.txt"), ("所有文件", "*.*")]
        )
        
        if not file_path:
            return
            
        try:
            # 读取文件内容
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            
            # 显示文件内容到结果区域
            self._enqueue_text(f"===== 手动添加依赖文件: {os.path.basename(file_path)} =====")
            self._enqueue_text(content)
            self._enqueue_text("=" * 60)
            
            # 将文件路径追加到依赖列表下拉框
            current_values = list(self.deps_list_cb.cget('values') or [])
            if file_path not in current_values:
                current_values.append(file_path)
                self._enqueue_deps_values(sorted(current_values))
                # 自动选择新添加的文件
                self.deps_list_var.set(file_path)
                self._enqueue_text(f"已将 {os.path.basename(file_path)} 添加到依赖列表")
            else:
                self._enqueue_text(f"{os.path.basename(file_path)} 已在依赖列表中")
                
        except Exception as e:
            self._enqueue_text(f"手动添加依赖文件失败: {e}")
            self._show_dark_error("❌ 文件读取错误", f"读取文件失败: {e}", 
                                 f"文件路径: {file_path}\n错误信息: {e}\n\n"
                                 f"可能的原因:\n• 文件不存在或路径错误\n• 文件权限不足\n• 文件格式不支持\n\n"
                                 f"解决方案:\n1. 检查文件路径是否正确\n2. 确保文件有读取权限\n3. 选择支持的文件格式（.txt, .py等）")

    def delete_customnodes_dir(self):
        """删除当前选中的CustomNodes目录（从历史中移除并清空选择）。"""
        current = self.custom_nodes_var.get().strip()
        if not current:
            self._show_dark_warning("⚠️ 无选择项提示", "当前未选择任何CustomNodes目录！", 
                                   "CustomNodes目录选择框为空，没有可删除的历史项。\n请先在下拉框中选择一个历史目录。")
            return
        # 使用暗色确认对话框
        if self._show_dark_confirm("确认删除", f"确定删除该插件目录历史项？\n\n{current}"):
            try:
                self.custom_nodes_history = [p for p in self.custom_nodes_history if p != current]
                self.custom_nodes_cb.configure(values=self.custom_nodes_history)
                self.custom_nodes_var.set("")
                self.save_config()
                self.update_result_text("[插件维护] 已删除插件目录历史项并清空当前选择")
                # 清空依赖列表显示
                self.deps_list_cb.configure(values=[])
            except Exception as e:
                self.update_result_text(f"[插件维护] 删除失败: {e}")

    # ---------------- 插件维护：手动触发检测 ----------------
    def detect_dependencies(self):
        """点击【检测依赖】按钮：带进度条、子线程扫描"""
        path = self.custom_nodes_var.get().strip()
        if not path:
            self._show_dark_warning("⚠️ 目录选择警告", "请先选择或浏览CustomNodes目录！", 
                                   "CustomNodes目录输入框为空，无法进行检测。\n请在下拉框中选择历史目录或点击【浏览】按钮选择目录。")
            return
        if not os.path.isdir(path):
            self._show_dark_warning("⚠️ 目录无效警告", f"目录不存在！\n\n路径: {path}", 
                                   "指定的目录路径不存在或无法访问。\n请检查路径是否正确，或选择其他有效目录。")
            return
        self._enqueue_text("[插件维护] 开始检测依赖...")
        # 进度条将在子线程里通过回调实时推进，无需手动步进
        Thread(target=self._scan_customnodes_async, args=(path,), daemon=True).start()

    def _progress_enqueue(self, value: float):
        """子线程安全更新进度条"""
        self._ui_queue.put(('progress', value))

    def _text_enqueue(self, text: str):
        """子线程安全追加文本"""
        self._ui_queue.put(('text', text))

    def _list_dependency_files(self, dir_path: str):
        """仅收集一级目录及根目录的依赖文件绝对路径，更新下拉框，不扫描安装状态"""
        if not os.path.isdir(dir_path):
            self._enqueue_text("[插件维护] 目录不存在")
            return
        files = []
        # 根目录：仅保留 requirements*.txt
        for name in os.listdir(dir_path):
            full = os.path.join(dir_path, name)
            if os.path.isfile(full) and (name == "requirements.txt" or (name.startswith("requirements") and name.endswith(".txt"))):
                files.append(full)
        # 一级子目录：同样只收 requirements*.txt
        for sub in os.listdir(dir_path):
            sub_path = os.path.join(dir_path, sub)
            if os.path.isdir(sub_path):
                try:
                    for name in os.listdir(sub_path):
                        full = os.path.join(sub_path, name)
                        if os.path.isfile(full) and (name == "requirements.txt" or (name.startswith("requirements") and name.endswith(".txt"))):
                            files.append(full)
                except Exception:
                    pass
        # 去重并过滤掉“已全部安装”的缓存文件
        cached = getattr(self, '_fully_installed', {}).get(dir_path, set())
        filtered = [p for p in files if p not in cached]
        self._enqueue_deps_values(sorted(filtered))
        # 静默更新，不在结果框打印文件列表

    # ---------------- 功能占位 / 后端调用 ----------------
    def test_mirror_speed(self):
        Thread(target=lambda: self.update_result_text(self.tools.test_mirror_speed(self.python_exe_path, self.mirror_var.get()))).start()

    def start_checking(self):
        req_path = self.deps_list_var.get()
        plugin_dir = self.custom_nodes_var.get()
        # 验证输入
        if not req_path:
            self._show_dark_warning("⚠️ 输入验证", "请先选择要模拟的依赖文件")
            return
        if not self.python_exe_path:
            self._show_dark_warning("⚠️ 输入验证", "请先选择Python环境")
            return
        
        # 检查Python环境和插件目录是否一致
        if plugin_dir and not self._same_environment_root(self.python_exe_path, plugin_dir):
            # 获取更详细的路径分析
            py_drive, py_path = os.path.splitdrive(os.path.abspath(self.python_exe_path))
            plug_drive, plug_path = os.path.splitdrive(os.path.abspath(plugin_dir))
            
            details = f"路径分析:\n"
            details += f"Python环境盘符: {py_drive.upper()}\n"
            details += f"插件目录盘符: {plug_drive.upper()}\n"
            details += f"Python环境一级目录: {py_path.split(os.sep)[1] if len(py_path.split(os.sep)) > 1 else 'N/A'}\n"
            details += f"插件目录一级目录: {plug_path.split(os.sep)[1] if len(plug_path.split(os.sep)) > 1 else 'N/A'}\n\n"
            details += f"可能的问题:\n"
            details += f"• 安装包可能无法正确识别插件路径\n"
            details += f"• 依赖关系可能无法正确解析\n"
            details += f"• 环境变量可能配置错误\n\n"
            details += f"解决方案:\n"
            details += f"1. 选择相同盘符下的Python环境和插件目录\n"
            details += f"2. 确保一级目录名称相同\n"
            details += f"3. 或重新选择匹配的Python环境"
            
            self._show_dark_warning(
                "⚠️ 环境不一致警告", 
                f"Python环境路径与插件目录不在同一根目录下，可能导致安装问题！",
                details
            )
            return
            
        # 显示进度条（统一使用队列事件）
        self._enqueue_progress_show(0.0)
        
        self._text_enqueue("[依赖检测] � 开始检测依赖安装情况...")

        def _task():
            try:
                self._enqueue_progress(0.1)
                text = self.tools.check_dependencies(
                    req_path,
                    self.python_exe_path,
                    plugin_dir,
                    progress_cb=lambda v: self._enqueue_progress(0.1 + 0.8 * float(v))
                )
                # 解析未安装项并缓存供“模拟安装”跳过使用
                try:
                    missing = []
                    lines = (text or '').splitlines()
                    flag = False
                    for ln in lines:
                        if ln.startswith('未安装:'):
                            flag = True
                            continue
                        if flag:
                            if ln.startswith('  - '):
                                name = ln[4:].strip()
                                if name:
                                    missing.append(name)
                            else:
                                break
                    self._last_missing_packages = missing
                except Exception:
                    self._last_missing_packages = []
                # 同时缓存原始规格（含版本）未安装列表，便于 dry-run 更贴近真实安装
                try:
                    self._last_missing_specs = self.tools.compute_missing_specs(req_path, self.python_exe_path, plugin_dir)
                except Exception:
                    self._last_missing_specs = []
                self._enqueue_progress(0.9)
                self.update_result_text(text)
                self._text_enqueue("[依赖检测] ✅ 依赖检测完成！")
                
            except Exception as e:
                self._text_enqueue(f"[依赖检测] ❌ 检测过程出错: {e}")
            finally:
                self._enqueue_progress(1.0)
                self._enqueue_progress_hide()

        Thread(target=_task).start()

    def start_simulation(self):
        req_path = self.deps_list_var.get()
        plugin_dir = self.custom_nodes_var.get()
        
        # 输入验证
        if not req_path:
            self._show_dark_warning("⚠️ 输入验证", "请先选择要模拟的依赖文件")
            return
        if not self.python_exe_path:
            self._show_dark_warning("⚠️ 输入验证", "请先选择Python环境")
            return
        
        # 检查Python环境和插件目录是否一致
        if plugin_dir and not self._same_environment_root(self.python_exe_path, plugin_dir):
            # 获取更详细的路径分析
            py_drive, py_path = os.path.splitdrive(os.path.abspath(self.python_exe_path))
            plug_drive, plug_path = os.path.splitdrive(os.path.abspath(plugin_dir))
            
            details = f"路径分析:\n"
            details += f"Python环境盘符: {py_drive.upper()}\n"
            details += f"插件目录盘符: {plug_drive.upper()}\n"
            details += f"Python环境一级目录: {py_path.split(os.sep)[1] if len(py_path.split(os.sep)) > 1 else 'N/A'}\n"
            details += f"插件目录一级目录: {plug_path.split(os.sep)[1] if len(plug_path.split(os.sep)) > 1 else 'N/A'}\n\n"
            details += f"可能的问题:\n"
            details += f"• 模拟安装可能无法正确识别插件路径\n"
            details += f"• 依赖关系可能无法正确解析\n"
            details += f"• 环境变量可能配置错误\n\n"
            details += f"解决方案:\n"
            details += f"1. 选择相同盘符下的Python环境和插件目录\n"
            details += f"2. 确保一级目录名称相同\n"
            details += f"3. 或重新选择匹配的Python环境"
            
            self._show_dark_warning(
                "⚠️ 环境不一致警告", 
                f"Python环境路径与插件目录不在同一根目录下，模拟安装可能出现问题！",
                details
            )
            return
            
        # 显示进度条
        try:
            self.progress_bar.pack(fill='x', pady=(8, 0))
            self.progress_bar.set(0.0)
        except Exception:
            pass

        self._text_enqueue("[模拟安装] 🚀 开始模拟安装预检...")

        def _task():
            try:
                self._enqueue_progress(0.1)
                

                use_missing_only = bool(getattr(self, 'skip_check_var', None) and self.skip_check_var.get())
                cached_missing_specs = list(getattr(self, '_last_missing_specs', []) or [])
                
                if use_missing_only and cached_missing_specs:
                    self._text_enqueue(f"[模拟安装] 📋 检测到{len(cached_missing_specs)}个未安装包，仅模拟这些包...")
                    text = self.tools.simulate_install_missing(
                        cached_missing_specs, 
                        self.python_exe_path, 
                        self.mirror_var.get(),
                        progress_cb=lambda v: self._enqueue_progress(0.1 + 0.8 * v)
                    )
                else:
                    self._text_enqueue("[模拟安装] 📋 正在解析依赖文件并进行完整模拟...")
                    text = self.tools.simulate_install(
                        req_path, 
                        self.python_exe_path, 
                        plugin_dir,
                        progress_cb=lambda v: self._enqueue_progress(0.1 + 0.8 * v)
                    )
                
                self._enqueue_progress(0.9)
                self.update_result_text(text)
                self._text_enqueue("[模拟安装] ✅ 模拟安装完成！")
                
            except Exception as e:
                self._text_enqueue(f"[模拟安装] ❌ 模拟过程出错: {e}")
            finally:
                self._enqueue_progress(1.0)
                self._enqueue_progress_hide()

        Thread(target=_task).start()

    def view_current_env(self):
        """查看当前Python环境已安装的包"""
        # 检查是否选择了Python环境
        if not self.python_exe_path:
            self._show_dark_warning("⚠️ 输入验证", "未选择Python环境，请先选择一个有效的Python环境")
            return
            
        # 显示进度条
        try:
            self.progress_bar.pack(fill='x', pady=(8, 0))
            self.progress_bar.set(0.0)
        except Exception:
            pass
        
        self._text_enqueue("[查看环境] 🔍 正在获取当前Python环境已安装的包...")
        
        # 在新线程中执行，避免界面卡顿
        def _task():
            try:
                self._enqueue_progress(0.1)
                result = self.tools.view_current_env(self.python_exe_path)
                self._enqueue_progress(0.7)
                
                # 格式化显示结果，使其更加用户友好
                if result and not result.startswith("查看当前环境失败") and not result.startswith("查看当前环境超时"):
                    # 解析包列表并统计
                    packages = [line.strip() for line in result.strip().split('\n') if line.strip() and not line.startswith('#')]
                    package_count = len(packages)
                    
                    # 创建友好的显示格式
                    friendly_result = f"当前Python环境: {self.python_exe_path}\n"
                    friendly_result += f"共安装了 {package_count} 个包:\n\n"
                    
                    # 按字母顺序排序包列表
                    packages.sort()
                    
                    # 逐个显示包，带有进度更新
                    for i, package in enumerate(packages):
                        friendly_result += f"{package}\n"
                        # 每显示10个包更新一次进度
                        if (i + 1) % 10 == 0:
                            progress = 0.7 + (i + 1) / package_count * 0.15
                            self._enqueue_progress(min(progress, 0.85))
                    
                    self.update_result_text(friendly_result)
                else:
                    # 如果结果是错误信息，直接显示
                    self.update_result_text(result)
                
                self._text_enqueue("[查看环境] ✅ 环境查看完成！")
                
                # 询问用户是否保存环境信息
                self._enqueue_progress(0.9)
                # 传递原始结果用于保存
                self.after(100, lambda: self._ask_save_environment(result))
                
            except Exception as e:
                self._text_enqueue(f"[查看环境] ❌ 获取环境信息失败: {e}")
            finally:
                self._enqueue_progress(1.0)
                self._enqueue_progress_hide()
        
        Thread(target=_task).start()
    
    def _ask_save_environment(self, environment_text):
        """询问用户是否保存环境信息到文件"""
        answer = self._show_dark_confirm(
            "保存环境信息",
            "是否将当前Python环境的包列表保存到文本文件？"
        )
        
        if answer:
            # 生成文件名：当前日期时间 + python环境完整绝对路径（格式化）
            import time
            current_datetime = time.strftime('%Y%m%d_%H%M%S')
            
            # 获取Python环境的目录路径（去掉python.exe部分）
            import os
            env_absolute_path = os.path.dirname(self.python_exe_path) if self.python_exe_path else "unknown_env"
            
            # 处理路径格式：将盘符从X:改为X盘，保持完整路径结构
            formatted_path = env_absolute_path
            if len(env_absolute_path) >= 2 and env_absolute_path[1] == ':':
                drive_letter = env_absolute_path[0]
                rest_path = env_absolute_path[2:] if len(env_absolute_path) > 2 else ''
                formatted_path = f"{drive_letter}盘{rest_path}"
            
            # 替换路径中的反斜杠为连字符，并处理其他无效字符
            formatted_env_path = formatted_path.replace('\\', '-').replace('/', '-').replace(':', '-').replace('*', '-').replace('?', '-').replace('"', '-').replace('<', '-').replace('>', '-').replace('|', '-')
            
            file_path = self._ask_saveas_filename_dark(
                title="保存环境信息",
                filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
                defaultextension=".txt",
                initialfile=f"{current_datetime}_{formatted_env_path}.txt"
            )
            
            if file_path:
                try:
                    with open(file_path, 'w', encoding='utf-8') as f:
                        # 写入环境信息头部
                        f.write(f"# Python环境: {self.python_exe_path}\n")
                        f.write(f"# 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                        
                        # 解析包列表并统计数量
                        packages = [line.strip() for line in environment_text.strip().split('\n') if line.strip() and not line.startswith('#')]
                        package_count = len(packages)
                        f.write(f"# 共安装了 {package_count} 个包\n")
                        f.write("#\n# 包列表:\n")
                        
                        # 写入包列表
                        for package in packages:
                            f.write(f"{package}\n")
                    
                    self._text_enqueue(f"[查看环境] 💾 环境信息已保存到: {os.path.basename(file_path)}")
                    # 显示保存成功消息（使用暗色信息框）
                    self._show_dark_info("✅ 保存成功", f"环境信息已保存到: {file_path}", 
                                        f"文件路径: {file_path}\n文件大小: {os.path.getsize(file_path) if os.path.exists(file_path) else '未知'} 字节\n\n"
                                        f"保存的内容包含:\n• 已安装的包列表\n• 包版本信息\n• 环境路径信息")
                    
                except Exception as e:
                    self._text_enqueue(f"[查看环境] ❌ 保存文件失败: {e}")
                    self._show_dark_error("❌ 文件保存错误", f"保存文件失败: {e}", 
                                         f"文件路径: {file_path}\n错误信息: {e}\n\n"
                                         f"可能的原因:\n• 文件路径无效或权限不足\n• 磁盘空间不足\n• 文件正在被其他程序使用\n\n"
                                         f"解决方案:\n1. 检查文件路径是否有效\n2. 确保有写入权限\n3. 检查磁盘空间是否充足")
    
    def _format_path_for_filename(self, path):
        """将路径格式化为合法的文件名字符串"""
        if not path:
            return "unknown_env"
        
        # 获取路径的基础名称
        base_name = os.path.basename(path)
        if not base_name or base_name == 'python.exe':
            # 如果基础名称是python.exe，使用父目录名称
            parent_dir = os.path.basename(os.path.dirname(path))
            base_name = parent_dir if parent_dir else "python_env"
        
        # 替换不合法的文件名字符
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            base_name = base_name.replace(char, '_')
        
        # 限制长度
        if len(base_name) > 50:
            base_name = base_name[:50]
        
        return base_name or "python_env"

    def start_installation(self):
        req_path = self.deps_list_var.get()
        plugin_dir = self.custom_nodes_var.get()
        
        # 检查是否选择了依赖文件
        if not req_path:
            self._show_dark_warning("⚠️ 输入验证", "请先选择要安装的依赖文件")
            return
            
        # 检查是否设置了Python环境
        if not self.python_exe_path:
            self._show_dark_warning("⚠️ 输入验证", "请先选择Python环境")
            return
        
        # 检查Python环境和插件目录是否一致
        if plugin_dir and not self._same_environment_root(self.python_exe_path, plugin_dir):
            # 获取更详细的路径分析
            py_drive, py_path = os.path.splitdrive(os.path.abspath(self.python_exe_path))
            plug_drive, plug_path = os.path.splitdrive(os.path.abspath(plugin_dir))
            
            details = f"路径分析:\n"
            details += f"Python环境盘符: {py_drive.upper()}\n"
            details += f"插件目录盘符: {plug_drive.upper()}\n"
            details += f"Python环境一级目录: {py_path.split(os.sep)[1] if len(py_path.split(os.sep)) > 1 else 'N/A'}\n"
            details += f"插件目录一级目录: {plug_path.split(os.sep)[1] if len(plug_path.split(os.sep)) > 1 else 'N/A'}\n\n"
            details += f"可能的问题:\n"
            details += f"• 实际安装可能无法正确识别插件路径\n"
            details += f"• 依赖关系可能无法正确解析\n"
            details += f"• 环境变量可能配置错误\n\n"
            details += f"解决方案:\n"
            details += f"1. 选择相同盘符下的Python环境和插件目录\n"
            details += f"2. 确保一级目录名称相同\n"
            details += f"3. 或重新选择匹配的Python环境"
            
            self._show_dark_warning(
                "⚠️ 环境不一致警告", 
                f"Python环境路径与插件目录不在同一根目录下，实际安装可能出现问题！",
                details
            )
            return
            
        # 显示进度条
        try:
            self.progress_bar.pack(fill='x', pady=(8, 0))
            self.progress_bar.set(0.05)
        except Exception:
            pass
            
        self._text_enqueue("[实际安装] 🚀 开始实际安装依赖...")
            
        def _task():
            try:
                self._enqueue_progress(0.1)
                
                # 检查是否跳过已安装检测
                use_missing_only = bool(getattr(self, 'skip_check_var', None) and self.skip_check_var.get())
                cached_missing_specs = list(getattr(self, '_last_missing_specs', []) or [])
                
                if use_missing_only and cached_missing_specs:
                    self._text_enqueue(f"[实际安装] 📋 检测到{len(cached_missing_specs)}个未安装包，仅安装这些包...")
                    result = self.tools.actual_install_missing(
                        cached_missing_specs,
                        self.python_exe_path,
                        self.mirror_var.get(),
                        progress_cb=lambda v: self._enqueue_progress(0.1 + 0.8 * v)
                    )
                else:
                    # 使用进度回调的实际安装函数
                    result = self.tools.actual_install(
                        req_path, 
                        self.python_exe_path, 
                        plugin_dir, 
                        self.mirror_var.get(),
                        progress_cb=lambda v: self._enqueue_progress(0.1 + 0.8 * v)
                    )
                
                self._enqueue_progress(0.9)
                self._enqueue_text(result)
                self._text_enqueue("[实际安装] ✅ 安装操作完成！")
                
            except Exception as e:
                self._text_enqueue(f"[实际安装] ❌ 安装过程出错: {e}")
                self._text_enqueue("[实际安装] 💡 建议：检查网络连接或使用'模拟安装'预检")
            finally:
                self._enqueue_progress(1.0)
                self._enqueue_progress_hide()
                
        Thread(target=_task).start()

    def compare_environment_files(self):
        """比较两个环境文件的差异"""
        # 显示进度条
        try:
            self.progress_bar.pack(fill='x', pady=(8, 0))
            self.progress_bar.set(0.0)
        except Exception:
            pass
        
        self._text_enqueue("[环境比较] 📋 开始比较环境文件...")
        
        # 在新线程中执行比较，避免界面卡顿
        def _task():
            try:
                self._enqueue_progress(0.1)
                
                # 选择第一个文件（安装前的环境文件）
                self._text_enqueue("[环境比较] 📁 请选择第一个环境快照文件...")
                file_a = self._ask_open_filename_dark(
                    title="选择环境快照文件 A", 
                    filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")]
                )
                if not file_a:
                    self._text_enqueue("[环境比较] ❌ 未选择第一个文件，比较取消")
                    self._enqueue_progress_hide()
                    return
                
                self._enqueue_progress(0.3)
                self._text_enqueue(f"[环境比较] ✅ 已选择文件A: {os.path.basename(file_a)}")
                
                # 选择第二个文件（安装后的环境文件）
                self._text_enqueue("[环境比较] 📁 请选择第二个环境快照文件...")
                file_b = self._ask_open_filename_dark(
                    title="选择环境快照文件 B", 
                    filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")]
                )
                if not file_b:
                    self._text_enqueue("[环境比较] ❌ 未选择第二个文件，比较取消")
                    self._enqueue_progress_hide()
                    return
                
                self._enqueue_progress(0.5)
                self._text_enqueue(f"[环境比较] ✅ 已选择文件B: {os.path.basename(file_b)}")
                self._text_enqueue("[环境比较] 🔍 正在比较两个环境文件...")
                
                # 执行比较
                result = self.tools.compare_environment_files(file_a, file_b)
                
                self._enqueue_progress(0.9)
                self.update_result_text(result)
                self._text_enqueue("[环境比较] ✅ 环境文件比较完成！")
                
            except Exception as e:
                self._text_enqueue(f"[环境比较] ❌ 比较过程出错: {e}")
            finally:
                self._enqueue_progress(1.0)
                self._enqueue_progress_hide()
        
        Thread(target=_task).start()

    def find_conflicting_libraries(self):
        Thread(target=lambda: self.update_result_text(self.tools.find_conflicts())).start()

    def start_environment_migration(self):
        """开始环境升级迁移 - 提供两种迁移方式"""
        # 检查是否有可用的Python环境
        if not self.python_exe_path:
            self._show_dark_warning("⚠️ 输入验证", "未选择Python环境，请先选择一个有效的Python环境")
            return
            
        # 显示进度条
        try:
            self.progress_bar.pack(fill='x', pady=(8, 0))
            self.progress_bar.set(0.0)
        except Exception:
            pass
        
        self._text_enqueue("[环境迁移] 🚀 开始环境升级迁移...")
        
        # 首先让用户选择迁移方式
        self._text_enqueue("[环境迁移] 💡 请选择迁移方式...")
        
        # 添加测试信息 - 显示当前python环境状态
        self._text_enqueue(f"[调试] 当前Python环境: {self.python_exe_path}")
        self._text_enqueue(f"[调试] 可用Python环境列表: {len(self.python_paths)}个")
        for i, path in enumerate(self.python_paths):
            self._text_enqueue(f"[调试] 环境{i+1}: {path}")
        
        # 如果当前没有可用的Python环境，显示友好的提示对话框
        if len(self.python_paths) < 2:
            # 创建友好的提示对话框
            dialog = ctk.CTkToplevel(self)
            dialog.title("环境迁移提示")
            dialog.geometry("500x250")
            dialog.transient(self)
            dialog.grab_set()
            
            # 设置暗色标题栏
            self._set_dark_titlebar(dialog)
            
            # 居中显示
            dialog.update_idletasks()
            x = (dialog.winfo_screenwidth() - dialog.winfo_width()) // 2
            y = (dialog.winfo_screenheight() - dialog.winfo_height()) // 2
            dialog.geometry(f"+{x}+{y}")
            
            # 主容器
            main_frame = ctk.CTkFrame(dialog)
            main_frame.pack(fill='both', expand=True, padx=20, pady=20)
            
            # 图标和标题
            title_frame = ctk.CTkFrame(main_frame)
            title_frame.pack(fill='x', pady=(0, 15))
            ctk.CTkLabel(title_frame, text="⚠️", font=ctk.CTkFont(size=24)).pack(side='left', padx=(0, 10))
            ctk.CTkLabel(title_frame, text="需要更多Python环境", font=ctk.CTkFont(size=16, weight="bold")).pack(side='left')
            
            # 说明文本
            info_frame = ctk.CTkFrame(main_frame)
            info_frame.pack(fill='x', pady=15)
            info_text = "环境目录迁移需要至少2个Python环境。\n\n您当前只有1个环境，可以：\n• 先添加另一个Python环境再使用目录迁移\n• 或者直接使用环境文件迁移（推荐）"
            ctk.CTkLabel(info_frame, text=info_text, text_color="white", justify="left", 
                        font=ctk.CTkFont(size=12)).pack(pady=8, padx=10)
            
            # 按钮区域
            button_frame = ctk.CTkFrame(main_frame)
            button_frame.pack(pady=(15, 0))
            
            def add_environment():
                dialog.destroy()
                # 延迟执行添加环境操作
                self.after(100, self.select_python_environment)
                self.after(200, self.start_environment_migration)
            
            def use_snapshot():
                dialog.destroy()
                self.after(100, self._perform_snapshot_migration)
            
            def cancel_all():
                dialog.destroy()
                self._text_enqueue("[环境迁移] ⚠️ 用户取消了迁移操作")
                self._enqueue_progress_hide()
            
            ctk.CTkButton(button_frame, text="添加环境", command=add_environment, width=100, 
                         font=ctk.CTkFont(size=12)).pack(side='left', padx=5)
            ctk.CTkButton(button_frame, text="使用文件迁移", command=use_snapshot, width=100, 
                         font=ctk.CTkFont(size=12), fg_color="green").pack(side='left', padx=5)
            ctk.CTkButton(button_frame, text="取消", command=cancel_all, width=80, 
                         font=ctk.CTkFont(size=12)).pack(side='left', padx=5)
            
            # 等待对话框关闭
            self.wait_window(dialog)
            return
        
        # 直接显示迁移方式选择对话框
        self._show_migration_mode_dialog()
    
    def _show_migration_mode_dialog(self):
        """显示迁移方式选择对话框 - 完整的双模式选择界面"""
        try:
            # 调试信息
            self._text_enqueue(f"[调试] python_paths数量: {len(self.python_paths)}")
            self._text_enqueue(f"[调试] python_paths内容: {self.python_paths}")
            
            # 检查是否有多个环境可用
            has_multiple_envs = len(self.python_paths) >= 2
            self._text_enqueue(f"[调试] 是否有多个环境: {has_multiple_envs}")
            
            # 创建完整的迁移模式选择对话框
            dialog = ctk.CTkToplevel(self)
            dialog.title("环境迁移方式选择")
            dialog.geometry("500x350")
            dialog.transient(self)
            dialog.grab_set()
            
            # 设置暗色标题栏
            self._set_dark_titlebar(dialog)
            
            # 居中显示
            dialog.update_idletasks()
            x = (dialog.winfo_screenwidth() - dialog.winfo_width()) // 2
            y = (dialog.winfo_screenheight() - dialog.winfo_height()) // 2
            dialog.geometry(f"+{x}+{y}")
            
            # 标题
            ctk.CTkLabel(dialog, text="请选择环境迁移方式：", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=20)
            
            # 迁移模式变量
            migration_mode = ctk.StringVar(value="env_to_env" if has_multiple_envs else "snapshot")
            
            # 创建选项容器
            options_frame = ctk.CTkFrame(dialog)
            options_frame.pack(fill='both', padx=30, pady=10, expand=True)
            
            # 环境目录迁移选项（仅在有多环境时显示）
            if has_multiple_envs:
                env_frame = ctk.CTkFrame(options_frame)
                env_frame.pack(fill='x', pady=8, padx=10)
                ctk.CTkRadioButton(env_frame, text="环境目录迁移", variable=migration_mode, value="env_to_env", font=ctk.CTkFont(size=12)).pack(side='left', padx=10)
                ctk.CTkLabel(env_frame, text="读取环境目录，在两个Python环境之间迁移包", text_color="gray", font=ctk.CTkFont(size=11)).pack(side='left', padx=10)
            
            # 环境快照迁移选项
            snapshot_frame = ctk.CTkFrame(options_frame)
            snapshot_frame.pack(fill='x', pady=8, padx=10)
            ctk.CTkRadioButton(snapshot_frame, text="环境文件迁移", variable=migration_mode, value="snapshot", font=ctk.CTkFont(size=12)).pack(side='left', padx=10)
            ctk.CTkLabel(snapshot_frame, text="读取环境文件，使用保存的环境快照进行迁移", text_color="gray", font=ctk.CTkFont(size=11)).pack(side='left', padx=10)
            
            # 说明文本
            info_frame = ctk.CTkFrame(dialog)
            info_frame.pack(fill='x', padx=30, pady=10)
            info_text = "💡 环境目录迁移：选择两个Python环境，自动对比并迁移缺失的包\n💡 环境文件迁移：选择之前保存的环境快照文件，应用到当前环境"
            ctk.CTkLabel(info_frame, text=info_text, text_color="white", justify="left", font=ctk.CTkFont(size=12)).pack(pady=8, padx=10)
            
            # 按钮区域
            button_frame = ctk.CTkFrame(dialog)
            button_frame.pack(pady=15)
            
            # 用于跟踪对话框状态的变量
            dialog_result = {"cancelled": True, "mode": None}
            
            def on_confirm():
                dialog_result["cancelled"] = False
                dialog_result["mode"] = migration_mode.get()
                dialog.destroy()
                
                # 延迟执行迁移操作，确保对话框完全关闭
                self.after(100, lambda: self._execute_migration_mode(dialog_result["mode"]))
            
            def on_cancel():
                dialog_result["cancelled"] = True
                dialog.destroy()
                # 延迟处理取消操作
                self.after(100, lambda: self._handle_migration_cancel())
            
            ctk.CTkButton(button_frame, text="确定", command=on_confirm, width=100, font=ctk.CTkFont(size=12)).pack(side='left', padx=10)
            ctk.CTkButton(button_frame, text="取消", command=on_cancel, width=100, font=ctk.CTkFont(size=12)).pack(side='left', padx=10)
            
            # 等待对话框关闭
            self.wait_window(dialog)
            
            # 如果对话框被取消且没有设置模式，处理取消操作
            if dialog_result["cancelled"] or dialog_result["mode"] is None:
                self._handle_migration_cancel()
                
        except Exception as e:
            self._text_enqueue(f"[环境迁移] ❌ 显示迁移方式选择失败: {e}")
            self._enqueue_progress_hide()
    
    def _execute_migration_mode(self, mode):
        """执行选定的迁移模式"""
        if mode == "env_to_env":
            self._perform_environment_directory_migration()
        elif mode == "snapshot":
            self._perform_snapshot_migration()
    
    def _handle_migration_cancel(self):
        """处理迁移取消操作"""
        self._text_enqueue("[环境迁移] ⚠️ 用户取消了迁移方式选择")
        self._enqueue_progress_hide()
    
    def _perform_environment_directory_migration(self):
        """执行环境目录迁移（原项目方式）"""
        try:
            self._enqueue_progress_show(0.05)
            
            # 创建源环境和目标环境选择对话框
            dialog = ctk.CTkToplevel(self)
            dialog.title("环境升级迁移")
            dialog.geometry("600x300")
            dialog.transient(self)
            dialog.grab_set()
            
            # 设置暗色标题栏
            self._set_dark_titlebar(dialog)
            
            # 居中显示
            dialog.update_idletasks()
            x = (dialog.winfo_screenwidth() - dialog.winfo_width()) // 2
            y = (dialog.winfo_screenheight() - dialog.winfo_height()) // 2
            dialog.geometry(f"+{x}+{y}")
            
            # 对话框结果变量
            dialog_result = {"source_env": None, "target_env": None, "cancelled": True}
            
            # 主容器
            main_frame = ctk.CTkFrame(dialog)
            main_frame.pack(fill='both', expand=True, padx=20, pady=20)
            
            # 标题
            ctk.CTkLabel(main_frame, text="选择源环境和目标环境", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(0, 15))
            
            # 源环境选择
            source_frame = ctk.CTkFrame(main_frame)
            source_frame.pack(fill='x', pady=8)
            ctk.CTkLabel(source_frame, text="源环境（要迁移的环境）:", font=ctk.CTkFont(size=12)).pack(side='left', padx=(0, 10))
            source_var = ctk.StringVar()
            source_combo = ctk.CTkComboBox(source_frame, variable=source_var, values=self.python_paths, width=350)
            source_combo.pack(side='left', fill='x', expand=True)
            if self.python_paths:
                source_combo.set(self.python_paths[0])
            
            # 目标环境选择
            target_frame = ctk.CTkFrame(main_frame)
            target_frame.pack(fill='x', pady=8)
            ctk.CTkLabel(target_frame, text="目标环境（要迁移到的环境）:", font=ctk.CTkFont(size=12)).pack(side='left', padx=(0, 10))
            target_var = ctk.StringVar()
            target_combo = ctk.CTkComboBox(target_frame, variable=target_var, values=self.python_paths, width=350)
            target_combo.pack(side='left', fill='x', expand=True)
            if len(self.python_paths) > 1:
                target_combo.set(self.python_paths[1])
            
            # 说明文本
            info_frame = ctk.CTkFrame(main_frame)
            info_frame.pack(fill='x', pady=15)
            info_text = "💡 此操作将把源环境中存在但目标环境中不存在的包安装到目标环境"
            ctk.CTkLabel(info_frame, text=info_text, text_color="white", justify="left", font=ctk.CTkFont(size=12)).pack(pady=8, padx=10)
            
            # 按钮区域
            button_frame = ctk.CTkFrame(main_frame)
            button_frame.pack(pady=(15, 0))
            
            def on_confirm():
                source_env = source_var.get()
                target_env = target_var.get()
                
                if not source_env or not target_env:
                    self._show_dark_warning("⚠️ 环境选择警告", "请选择源环境和目标环境！", 
                                           "源环境或目标环境未选择，无法继续迁移操作。\n请在两个下拉框中分别选择源环境和目标环境。")
                    return
                    
                if source_env == target_env:
                    self._show_dark_warning("⚠️ 环境选择错误", "源环境和目标环境不能相同！", 
                                           "源环境和目标环境选择了相同的路径，迁移操作没有意义。\n请选择不同的源环境和目标环境。")
                    return
                
                dialog_result["source_env"] = source_env
                dialog_result["target_env"] = target_env
                dialog_result["cancelled"] = False
                dialog.destroy()
                
            def on_cancel():
                dialog_result["cancelled"] = True
                dialog.destroy()
            
            ctk.CTkButton(button_frame, text="确定", command=on_confirm, width=100, font=ctk.CTkFont(size=12)).pack(side='left', padx=10)
            ctk.CTkButton(button_frame, text="取消", command=on_cancel, width=100, font=ctk.CTkFont(size=12)).pack(side='left', padx=10)
            
            # 等待对话框关闭
            self.wait_window(dialog)
            
            # 检查是否取消
            if dialog_result["cancelled"]:
                self._text_enqueue("[环境迁移] ⚠️ 用户取消了环境选择")
                return
                
            source_env = dialog_result["source_env"]
            target_env = dialog_result["target_env"]
            
            if not source_env or not target_env:
                self._text_enqueue("[环境迁移] ⚠️ 用户取消了环境选择")
                return
                
            if source_env == target_env:
                self._show_dark_warning("⚠️ 环境选择错误", "源环境和目标环境不能相同！", 
                                       "源环境和目标环境选择了相同的路径，迁移操作没有意义。\n请选择不同的源环境和目标环境。")
                return
                
            # 确认迁移（使用暗色确认对话框）
            confirm_result = self._show_dark_confirm(
                "确认环境迁移",
                f"您确定要将源环境 '{os.path.basename(source_env)}' 中的包迁移到目标环境 '{os.path.basename(target_env)}' 吗？\n\n"
                "此操作将在目标环境中安装源环境中存在但目标环境中不存在的包。"
            )
            
            if not confirm_result:
                self._text_enqueue("[环境迁移] ⚠️ 用户取消了迁移操作")
                return
                
            self._text_enqueue(f"[环境迁移] 📋 开始从 '{os.path.basename(source_env)}' 迁移到 '{os.path.basename(target_env)}' ...")
            
            # 执行迁移任务
            def _migration_task():
                try:
                    self._enqueue_progress(0.15)
                    
                    # 获取源环境包列表
                    self._text_enqueue(f"[环境迁移] 🔍 正在获取源环境 '{os.path.basename(source_env)}' 的包列表...")
                    source_packages = self._get_installed_packages(source_env)
                    if not source_packages:
                        self._text_enqueue("[环境迁移] ❌ 无法获取源环境中的包列表或源环境中没有已安装的包")
                        return
                    
                    self._enqueue_progress(0.3)
                    
                    # 获取目标环境包列表
                    self._text_enqueue(f"[环境迁移] 🔍 正在获取目标环境 '{os.path.basename(target_env)}' 的包列表...")
                    target_packages = self._get_installed_packages(target_env)
                    
                    self._enqueue_progress(0.45)
                    
                    # 计算需要安装的包（源有、目标没有）
                    packages_to_install = []
                    for package_name, package_version in source_packages.items():
                        if package_name.lower() not in [p.lower() for p in target_packages.keys()]:
                            packages_to_install.append((package_name, package_version))
                    
                    total_packages = len(packages_to_install)
                    self._text_enqueue(f"[环境迁移] 📊 找到 {total_packages} 个需要安装的包")
                    
                    if total_packages == 0:
                        self._text_enqueue("[环境迁移] ✅ 目标环境已经包含源环境中的所有包，无需迁移")
                        self._enqueue_progress(1.0)
                        return
                    
                    self._enqueue_progress(0.5)
                    
                    # 安装包到目标环境
                    success_count = 0
                    failed_packages = []
                    
                    for i, (package_name, package_version) in enumerate(packages_to_install):
                        progress = 0.5 + (i + 1) / max(1, total_packages) * 0.45
                        self._enqueue_progress(min(progress, 0.95))
                        
                        self._text_enqueue(f"[环境迁移] 📦 正在安装 {package_name}=={package_version} ... ({i+1}/{total_packages})")
                        success, reason = self._install_package_to_env(target_env, package_name, package_version)
                        if success:
                            success_count += 1
                            self._text_enqueue(f"[环境迁移] ✅ 安装成功: {package_name}=={package_version}")
                        else:
                            failed_packages.append((f"{package_name}=={package_version}", reason))
                            self._text_enqueue(f"[环境迁移] ❌ 安装失败: {package_name}=={package_version} | {reason}")
                    
                    # 显示结果
                    self._text_enqueue("="*60)
                    self._text_enqueue("[环境迁移] 🎉 环境迁移完成！")
                    self._text_enqueue(f"[环境迁移] ✅ 成功安装: {success_count} 个包")
                    self._text_enqueue(f"[环境迁移] ❌ 安装失败: {len(failed_packages)} 个包")
                    
                    if failed_packages:
                        self._text_enqueue("[环境迁移] 📋 失败的包列表(按原因归类):")
                        groups = {}
                        for pkg, reason in failed_packages:
                            key = reason or 'unknown error'
                            groups.setdefault(key, []).append(pkg)
                        for reason, pkgs in groups.items():
                            self._text_enqueue(f"  • {reason} ({len(pkgs)}):")
                            for pkg in pkgs:
                                self._text_enqueue(f"    - {pkg}")
                        self.after(100, lambda: self._ask_save_failed_packages(failed_packages))
                        
                except Exception as e:
                    self._text_enqueue(f"[环境迁移] ❌ 执行环境迁移时出错: {e}")
                finally:
                    self._enqueue_progress(1.0)
                    self._enqueue_progress_hide()
            
            # 启动迁移任务
            Thread(target=_migration_task).start()
            
        except Exception as e:
            self._text_enqueue(f"[环境迁移] ❌ 环境目录迁移初始化失败: {e}")
            self._enqueue_progress_hide()
    
    def _perform_snapshot_migration(self):
        """执行环境快照迁移（现有方式）"""
        bg_started = False
        try:
            self._enqueue_progress_show(0.05)
            
            # 允许用户选择使用快照文件进行迁移
            self._text_enqueue("[环境迁移] 📁 请选择环境快照文件（可选）...")
            snapshot = self._ask_open_filename_dark(
                title="选择环境快照文件(可选)", 
                filetypes=[("文本文件", "*.txt"), ("依赖文件", "requirements*.txt"), ("所有文件", "*.*")]
            )
            
            # 如果用户取消了文件选择，直接结束迁移
            if not snapshot:
                self._text_enqueue("[环境迁移] ❌ 用户取消了文件选择，迁移终止")
                self._enqueue_progress_hide()
                return
            
            self._enqueue_progress(0.2)
            self._text_enqueue(f"[环境迁移] 📋 已选择快照文件: {os.path.basename(snapshot)}")
            
            # 询问是否直接应用迁移
            if self._show_dark_confirm("迁移环境", f"检测到快照文件 {os.path.basename(snapshot)}，是否直接应用迁移(安装到当前环境)?"):
                self._text_enqueue("[环境迁移] 🔧 正在应用迁移到当前环境...")
                try:
                    with open(snapshot, 'r', encoding='utf-8', errors='replace') as f:
                        lines = f.readlines()
                    packages = []
                    for line in lines:
                        t = (line or '').strip()
                        if not t or t.startswith('#') or t.startswith('-'):
                            continue
                        if '==' in t or '>=' in t or '<=' in t:
                            packages.append(t)
                        else:
                            parts = t.split()
                            if len(parts) >= 2:
                                packages.append(f"{parts[0]}=={parts[1].lstrip('v')}")
                            elif len(parts) == 1:
                                packages.append(parts[0])
                    before = len(packages)
                    packages = sorted(list(set(packages)))
                    self._text_enqueue(f"[环境迁移] 📦 快照解析得到 {len(packages)} 个包 (去重前 {before})")
                    mirror_url = PYPI_MIRRORS.get(self.mirror_var.get(), '')
                    def _run():
                        try:
                            self._perform_env_list_restore(packages, snapshot, False, True, mirror_url)
                        except Exception as e:
                            self._text_enqueue(f"[环境迁移] 运行出错: {e}")
                    Thread(target=_run, daemon=True).start()
                    bg_started = True
                except Exception as e:
                    self._text_enqueue(f"[环境迁移] 快照解析失败: {e}")
            else:
                self._text_enqueue("[环境迁移] 📊 正在分析迁移计划...")
                result = self.tools.plan_migration_from_snapshot(snapshot, self.python_exe_path)
                self._text_enqueue("[环境迁移] ✅ 迁移计划分析完成！")
            
            if not bg_started:
                self._enqueue_progress(0.85)
                self.update_result_text(result)
                self._enqueue_progress(0.95)
            
        except Exception as e:
            self._text_enqueue(f"[环境迁移] ❌ 快照迁移过程出错: {e}")
        finally:
            if not bg_started:
                self._enqueue_progress(1.0)
                self._enqueue_progress_hide()

    def _get_installed_packages(self, python_env):
        """获取指定Python环境中已安装的包列表"""
        try:
            import json
            import subprocess
            
            cmd = [python_env, '-m', 'pip', 'list', '--format=json']
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0 and result.stdout:
                packages = json.loads(result.stdout)
                return {pkg['name']: pkg['version'] for pkg in packages}
            else:
                self._text_enqueue(f"[环境迁移] ❌ 获取包列表失败，返回代码: {result.returncode}")
                return {}
        except Exception as e:
            self._text_enqueue(f"[环境迁移] ❌ 获取包列表时出错: {e}")
            return {}
    
    def _install_package_to_env(self, python_env, package_name, package_version):
        """在指定Python环境中安装包，返回(success, reason)"""
        try:
            import subprocess
            cmd = [python_env, '-m', 'pip', 'install', f'{package_name}=={package_version}', '--no-deps']
            mirror_url = PYPI_MIRRORS.get(self.mirror_var.get(), '')
            if mirror_url:
                cmd.extend(['--index-url', mirror_url])
                host = mirror_url.split('/')[2]
                cmd.extend(['--trusted-host', host])
                cmd.extend(['--extra-index-url', 'https://pypi.org/simple'])
                cmd.extend(['--trusted-host', 'pypi.org'])
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            success = result.returncode == 0
            if success:
                return True, ''
            err_text = ((result.stderr or '') + '\n' + (result.stdout or '')).strip()
            lines = [l.strip() for l in err_text.split('\n') if l.strip() and not l.strip().startswith('WARNING')]
            summary = ''
            for l in reversed(lines[-6:]):
                if 'No matching distribution found' in l or 'Could not find a version that satisfies' in l:
                    summary = l
                    break
            if not summary:
                summary = lines[-1] if lines else 'unknown error'
            return False, summary
        except Exception as e:
            return False, str(e)
    
    def _ask_save_failed_packages(self, failed_packages):
        """询问是否保存失败包列表，按原因归类写入"""
        answer = self._show_dark_confirm(
            "保存失败包列表",
            f"有 {len(failed_packages)} 个包安装失败，是否保存失败包列表到文件？"
        )
        if answer:
            try:
                import time
                current_datetime = time.strftime('%Y%m%d_%H%M%S')
                file_path = self._ask_saveas_filename_dark(
                    title="保存失败包列表",
                    filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
                    defaultextension=".txt",
                    initialfile=f"{current_datetime}_failed_packages.txt"
                )
                if file_path:
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(f"# 安装失败的包列表\n")
                        f.write(f"# 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                        f.write(f"# 共 {len(failed_packages)} 个包安装失败\n")
                        f.write("#\n")
                        groups = {}
                        for item in failed_packages:
                            if isinstance(item, (list, tuple)) and len(item) == 2:
                                pkg, reason = item
                            else:
                                pkg, reason = str(item), ''
                            key = reason or 'unknown error'
                            groups.setdefault(key, []).append(pkg)
                        for reason, pkgs in groups.items():
                            f.write(f"# 原因: {reason} - {len(pkgs)} 个\n")
                            for pkg in pkgs:
                                f.write(f"{pkg}\n")
                            f.write("#\n")
                    self._show_dark_info("✅ 保存成功", f"失败包列表已保存到: {file_path}", 
                                        f"文件路径: {file_path}\n文件大小: {os.path.getsize(file_path) if os.path.exists(file_path) else '未知'} 字节\n\n"
                                        f"保存的内容包含:\n• 安装失败的包名称\n• 失败原因归类\n\n"
                                        f"您可以查看此文件并在下一步逐项处理或重试安装。")
            except Exception as e:
                self._show_dark_error("❌ 文件保存错误", f"保存失败包列表时出错: {e}", 
                                     f"错误信息: {e}\n\n"
                                     f"可能的原因:\n• 文件路径无效或权限不足\n• 磁盘空间不足\n• 文件正在被其他程序使用\n\n"
                                     f"解决方案:\n1. 检查文件路径是否有效\n2. 确保有写入权限\n3. 检查磁盘空间是否充足")
    
    def search_library_exact(self):
        lib_name = self.lib_name_var.get().strip()
        if not lib_name:
            # 使用自定义的暗色调对话框替代系统messagebox
            self._show_dark_warning("警告", "请输入要查找的库名称")
            return
        # 添加到历史记录
        self._add_to_lib_history(lib_name)
        
        def _search_and_update_versions():
            """执行搜索并更新版本列表"""
            result = self.tools.search_library_exact(lib_name)
            self.update_result_text(result)
            
            # 解析版本信息并更新版本下拉框
            versions = []
            for line in result.split('\n'):
                if '可用版本：' in line:
                    # 提取版本列表
                    version_part = line.split('可用版本：')[1].strip()
                    # 处理逗号分隔的版本列表
                    versions = [v.strip() for v in version_part.split(',') if v.strip()]
                    break
                elif 'Available versions:' in line:
                    # 英文版本信息
                    version_part = line.split('Available versions:')[1].strip()
                    versions = [v.strip() for v in version_part.split(',') if v.strip()]
                    break
            
            # 更新版本下拉框（在主线程中执行UI更新）
            if versions:
                self.after(0, lambda: self._update_version_combo(versions))
        
        Thread(target=_search_and_update_versions).start()
    
    def _update_version_combo(self, versions):
        """更新版本下拉框的选项"""
        # 限制版本数量，避免下拉框过长
        max_versions = 20
        display_versions = versions[:max_versions]
        
        # 更新下拉框选项
        self.version_cb.configure(values=display_versions)
        
        # 如果有版本，默认选择第一个（最新版本）
        if display_versions:
            self.version_var.set(display_versions[0])
        
        # 在状态栏显示版本数量信息
        if len(versions) > max_versions:
            self._text_enqueue(f"[库查找] 找到 {len(versions)} 个版本，显示前 {max_versions} 个")
        else:
            self._text_enqueue(f"[库查找] 找到 {len(versions)} 个可用版本")

    def search_library_local(self):
        lib_name = self.lib_name_var.get().strip()
        if not lib_name:
            # 使用自定义暗色调输入对话框获取用户输入
            search_term = self._show_dark_input_dialog("模糊查找", "请输入要模糊查找的库名称字符：")
            if search_term is None:  # 用户取消
                return
            lib_name = search_term.strip()
            if not lib_name:  # 输入为空
                return
            # 将输入的值设置到下拉框中
            self.lib_name_var.set(lib_name)
        # 添加到历史记录
        self._add_to_lib_history(lib_name)
        Thread(target=lambda: self.update_result_text(self.tools.search_library_fuzzy(lib_name))).start()

    def install_library(self):
        lib_name = self.lib_name_var.get().strip()
        if not lib_name:
            # 使用自定义的暗色调对话框替代系统messagebox
            self._show_dark_warning("警告", "请输入要安装的库名称")
            return
        # 添加到历史记录
        self._add_to_lib_history(lib_name)
        Thread(target=lambda: self.update_result_text(self.tools.install_library(lib_name, self.version_var.get(), self.python_exe_path, self.mirror_var.get()))).start()

    def uninstall_library(self):
        lib_name = self.lib_name_var.get().strip()
        if not lib_name:
            # 使用自定义的暗色调对话框替代系统messagebox
            self._show_dark_warning("警告", "请输入要卸载的库名称")
            return
        # 添加到历史记录
        self._add_to_lib_history(lib_name)
        Thread(target=lambda: self.update_result_text(self.tools.uninstall_library(lib_name, self.python_exe_path))).start()

    def install_whl_file(self):
        path = self._ask_open_filename_dark(title="选择whl文件", filetypes=[("Wheel", "*.whl"), ("所有文件", "*.*")])
        if path:
            Thread(target=lambda: self.update_result_text(self.tools.install_whl(path, self.python_exe_path))).start()

    def install_source_code(self):
        path = self._ask_open_filename_dark(title="选择源码压缩包", filetypes=[("源码压缩包", "*.zip;*.tar.gz;*.tar"), ("所有文件", "*.*")])
        if path:
            Thread(target=lambda: self.update_result_text(self.tools.install_from_source(path, self.python_exe_path, self.mirror_var.get()))).start()

    def execute_command(self):
        cmd = self.cmd_var.get().strip()
        if not cmd:
            self._show_dark_warning("⚠️ 命令输入警告", "请输入要执行的命令！", 
                                   "命令输入框为空，无法执行操作。\n请在CMD输入框中输入有效的命令。")
            return
        # 检查Python环境是否已选择（对于需要Python环境的命令）
        if not self.python_exe_path and ('pip' in cmd.lower() or 'python' in cmd.lower()):
            self._show_dark_warning("⚠️ Python环境未选择", 
                                    "执行pip或python相关命令需要先选择Python环境！",
                                    "当前未选择Python环境，无法执行pip或python命令。\n请先点击【选择】按钮选择Python环境。")
            return
        # 添加到历史记录
        self._add_to_cmd_history(cmd)
        Thread(target=lambda: self.update_result_text(self.tools.execute_command(cmd))).start()

    def show_pip_params(self):
        self.update_result_text(self.tools.pip_params_help())

    # ---------------- 前端占位动作（暂未实现后端） ----------------
    def _stub_batch_update(self):
        self.update_result_text("[插件维护] 批量更新：功能尚未实现，后续补充")

    def query_comfy_version(self):
        try:
            self._enqueue_progress_show(0.05)
            repo_path = self.comfy_dir_var.get().strip() or os.path.join(os.getcwd(), 'ComfyUI')
            if not os.path.isdir(repo_path):
                self._show_dark_warning("⚠️ 目录缺失", "未找到 ComfyUI 目录", f"路径: {repo_path}")
                self._enqueue_progress_hide()
                return
            def run_git(args):
                try:
                    r = subprocess.run(['git','-C',repo_path]+args, capture_output=True, text=True, errors='replace', timeout=20)
                    return r.returncode, (r.stdout or '').strip(), (r.stderr or '').strip()
                except Exception as e:
                    return 1, '', str(e)
            rc_b, branch, _ = run_git(['rev-parse','--abbrev-ref','HEAD'])
            rc_h, head, _ = run_git(['rev-parse','HEAD'])
            rc_lt, latest_tag, _ = run_git(['describe','--tags','--abbrev=0'])
            rc_desc, describe, _ = run_git(['describe','--tags','--always'])
            if any(rc != 0 for rc in [rc_b, rc_h]):
                try:
                    import pygit2
                    repo = pygit2.Repository(repo_path)
                    branch = repo.head.shorthand or ''
                    try:
                        head = str(repo.head.target)
                    except Exception:
                        head = ''
                    versions = []
                    try:
                        for k in repo.references:
                            try:
                                prefix = "refs/tags/v"
                                if k.startswith(prefix):
                                    v = k[len(prefix):].split(".")
                                    if len(v) >= 3:
                                        vi = (int(v[0]) * 10000000000 + int(v[1]) * 100000 + int(v[2]))
                                        versions.append((vi, k))
                            except Exception:
                                pass
                        versions.sort()
                        latest_tag = versions[-1][1].split('/')[-1] if versions else ''
                    except Exception:
                        latest_tag = ''
                    describe = latest_tag or (head[:8] if head else '')
                except Exception:
                    pass
            self._enqueue_progress(0.4)
            mode = '未知'
            if branch.lower() in ('master','main'):
                mode = '开发版'
            if latest_tag:
                if describe and latest_tag in describe:
                    mode = '稳定版'
            lines = []
            lines.append(f"[版本查询] 📁 ComfyUI: {repo_path}")
            lines.append(f"[版本查询] 🔀 分支: {branch or '未知'}")
            lines.append(f"[版本查询] 🔑 HEAD: {head[:8] if head else '未知'}")
            lines.append(f"[版本查询] 🏷️ 最新标签: {latest_tag or '无'}")
            lines.append(f"[版本查询] 📝 当前描述: {describe or '无'}")
            lines.append(f"[版本查询] 模式: {mode}")
            self.update_result_text("\n".join(lines))
            try:
                disp = latest_tag or (branch and branch.lower() in ('master','main') and (describe or head[:8]) ) or (describe or head[:8] or '')
                self.current_ver_var.set(f"当前: {disp or '未知'}")
            except Exception:
                pass
        except Exception as e:
            self.update_result_text(f"[版本查询] 异常: {e}")
        finally:
            self._enqueue_progress(1.0)
            self._enqueue_progress_hide()

    def _refresh_current_version_label(self):
        try:
            repo_path = self.comfy_dir_var.get().strip() or os.path.join(os.getcwd(), 'ComfyUI')
            if not os.path.isdir(repo_path):
                return
            
            # 检查是否是Git仓库
            is_git_repo = os.path.isdir(os.path.join(repo_path, '.git'))
            if not is_git_repo:
                self.current_ver_var.set(f"当前: 非Git仓库")
                return
            
            # 获取分支信息
            r2 = subprocess.run(['git','-C',repo_path,'rev-parse','--abbrev-ref','HEAD'], capture_output=True, text=True, errors='replace', timeout=10)
            branch = (r2.stdout or '').strip()
            
            # 获取HEAD哈希
            r3 = subprocess.run(['git','-C',repo_path,'rev-parse','HEAD'], capture_output=True, text=True, errors='replace', timeout=10)
            head = (r3.stdout or '').strip()
            
            # 检查是否是新初始化的仓库（没有任何提交）
            if r3.returncode != 0 or head == 'HEAD' or not head:
                self.current_ver_var.set(f"当前: 新仓库")
                return
            
            # 获取标签信息
            r1 = subprocess.run(['git','-C',repo_path,'describe','--tags','--abbrev=0'], capture_output=True, text=True, errors='replace', timeout=10)
            tag = (r1.stdout or '').strip()
            
            # 构建显示信息
            if tag:
                disp = tag
            elif branch and branch.lower() in ('master','main'):
                disp = f"{branch}@{head[:8]}" if head else branch
            elif head:
                disp = head[:8]
            else:
                disp = "未知"
            
            self.current_ver_var.set(f"当前: {disp}")
        except Exception as e:
            self.current_ver_var.set(f"当前: 检测失败")
            self._text_enqueue(f"[版本检测] 错误: {e}")

    def _stub_version_manage(self):
        try:
            repo = (self.comfy_dir_var.get() or '').strip()
            if not repo or not os.path.isdir(repo):
                self._show_dark_warning("⚠️ 目录无效", "请先选择有效的ComfyUI目录", f"当前: {repo or '未选择'}")
                return
            
            # 先显示对话框，再异步获取Git信息
            dialog = ctk.CTkToplevel(self)
            dialog.title("ComfyUI 版本管理")
            dialog.geometry("900x700")  # 增加窗口高度
            dialog.transient(self)
            dialog.grab_set()
            self._set_dark_titlebar(dialog)
            dialog.update_idletasks()
            x = (dialog.winfo_screenwidth() - dialog.winfo_width()) // 2
            y = (dialog.winfo_screenheight() - dialog.winfo_height()) // 2
            dialog.geometry(f"+{x}+{y}")

            main = ctk.CTkFrame(dialog)
            main.pack(fill='both', expand=True, padx=16, pady=16)

            top = ctk.CTkFrame(main)
            top.pack(fill='x')
            
            # 创建信息显示区域，先用占位符
            info_frame = ctk.CTkFrame(top)
            info_frame.pack(fill='x', pady=(0,8))
            
            remote_var = tk.StringVar(value="🌐 远端地址: 正在获取...")
            branch_var = tk.StringVar(value="📝 当前分支: 正在获取...    🔖 当前版本: 正在获取...")
            repo_var = tk.StringVar(value=f"📁 ComfyUI目录: {repo}")
            
            ctk.CTkLabel(info_frame, textvariable=remote_var, anchor='w').pack(anchor='w')
            ctk.CTkLabel(info_frame, textvariable=branch_var, anchor='w').pack(anchor='w')
            ctk.CTkLabel(info_frame, textvariable=repo_var, anchor='w', text_color='white', font=('', 14)).pack(anchor='w')
            



            




            





            # 创建表格容器，移除标签页
            table_container = ctk.CTkFrame(main)
            table_container.pack(fill='both', expand=True, pady=8)

            def async_get_git_info():
                try:
                    # 检查是否是Git仓库
                    is_git_repo = os.path.isdir(os.path.join(repo, '.git'))
                    if not is_git_repo:
                        remote_var.set(f"🌐 远端地址: 非Git仓库")
                        branch_var.set(f"📝 当前分支: 非Git仓库    🔖 当前版本: 非Git仓库")
                        self._text_enqueue(f"[版本维护] ⚠️ 未检测到Git仓库，{repo} 不是Git仓库")
                        return
                    
                    # 执行git status命令检查仓库状态
                    status_result = subprocess.run(['git','-C',repo,'status'], capture_output=True, text=True, errors='replace')
                    self._text_enqueue(f"[版本维护] 📋 Git状态: {status_result.stdout[:100]}...")
                    
                    # 获取远程仓库地址
                    remote = (subprocess.run(['git','-C',repo,'remote','get-url','origin'], capture_output=True, text=True, errors='replace').stdout or '').strip()
                    
                    # 获取分支信息
                    branch = (subprocess.run(['git','-C',repo,'rev-parse','--abbrev-ref','HEAD'], capture_output=True, text=True, errors='replace').stdout or '').strip()
                    
                    # 获取HEAD哈希
                    head_result = subprocess.run(['git','-C',repo,'rev-parse','HEAD'], capture_output=True, text=True, errors='replace')
                    head = (head_result.stdout or '').strip()
                    
                    # 获取版本描述
                    describe_result = subprocess.run(['git','-C',repo,'describe','--tags','--always'], capture_output=True, text=True, errors='replace')
                    describe = (describe_result.stdout or '').strip()
                    
                    # 处理detached HEAD状态
                    if branch == 'HEAD':
                        branch = 'detached HEAD'
                    
                    # 处理新初始化的仓库，没有任何提交的情况
                    if head_result.returncode != 0 or head == 'HEAD' or not head:
                        # 这是一个新初始化的Git仓库，没有任何提交
                        self._text_enqueue(f"[版本维护] ⚠️ 这是一个新初始化的Git仓库，尚未有任何提交")
                        # 更新UI显示
                        remote_var.set(f"🌐 远端地址: {remote or '未知'}")
                        branch_var.set(f"📝 当前分支: {branch or '未知'}    🔖 当前版本: 新仓库")
                        
                        # 在执行结果中显示详细信息
                        self._text_enqueue(f"[版本维护] 📁 ComfyUI目录: {repo}")
                        if remote:
                            self._text_enqueue(f"[版本维护] 📡 远程仓库地址: {remote}")
                        else:
                            self._text_enqueue(f"[版本维护] ⚠️ 未检测到有效的远程仓库地址")
                        
                        self._text_enqueue(f"[版本维护] 🔀 当前分支: {branch or '未知'}")
                        self._text_enqueue(f"[版本维护] 🔑 HEAD: 无提交")
                        self._text_enqueue(f"[版本维护] 🏷️ 当前版本: 新仓库 (未初始化)")
                        return
                    
                    # 处理HEAD获取异常的情况
                    if head == 'HEAD' or not head:
                        # 尝试使用log命令获取实际的HEAD哈希
                        log_result = subprocess.run(['git','-C',repo,'log','--oneline','-1'], capture_output=True, text=True, errors='replace')
                        log_line = (log_result.stdout or '').strip()
                        self._text_enqueue(f"[版本维护] ⚠️ HEAD获取异常，尝试使用log命令获取: '{log_line}'")
                        if log_line:
                            parts = log_line.split()
                            if parts:
                                head = parts[0]
                                self._text_enqueue(f"[版本维护] 📋 从log获取HEAD: {head}")
                    
                    # 更新UI显示
                    remote_var.set(f"🌐 远端地址: {remote or '未知'}")
                    branch_var.set(f"📝 当前分支: {branch or '未知'}    🔖 当前版本: {describe or head[:8]}")
                    
                    # 在执行结果中显示详细信息
                    self._text_enqueue(f"[版本维护] 📁 ComfyUI目录: {repo}")
                    if remote:
                        self._text_enqueue(f"[版本维护] 📡 远程仓库地址: {remote}")
                    else:
                        self._text_enqueue(f"[版本维护] ⚠️ 未检测到有效的远程仓库地址")
                    
                    self._text_enqueue(f"[版本维护] 🔀 当前分支: {branch or '未知'}")
                    self._text_enqueue(f"[版本维护] 🔑 HEAD: {head[:8] if head else '未知'}")
                    
                    if describe:
                        self._text_enqueue(f"[版本维护] 🏷️ 当前版本: {describe}")
                    elif head:
                        self._text_enqueue(f"[版本维护] 🏷️ 当前版本: {head[:8]} (无标签)")
                    else:
                        self._text_enqueue(f"[版本维护] ⚠️ 未检测到有效的版本信息")
                    
                except Exception as e:
                    remote_var.set(f"🌐 远端地址: 获取失败 - {e}")
                    branch_var.set(f"📝 当前分支: 获取失败    🔖 当前版本: 未知")
                    self._text_enqueue(f"[版本维护] ❌ 获取Git信息失败: {e}")
                    self._text_enqueue(f"[版本维护] 💡 可能原因: 目录不是Git仓库、Git命令未安装或无权限访问")

            # 在新线程中获取Git信息
            import threading
            git_info_thread = threading.Thread(target=async_get_git_info, daemon=True)
            git_info_thread.start()
            # 移除重复的info_frame和tabs创建代码
  
            def run_git(args):
                return subprocess.run(['git','-C',repo]+args, capture_output=True, text=True, errors='replace')

            def build_table(container, rows, current_ref_is_tag=False, max_rows=30, describe_var=None):
                try:
                    # 确保容器有效
                    if not hasattr(container, 'pack'):
                        raise Exception("无效的容器组件")
                    
                    # 添加缺失的变量定义
                    selected_var = tk.StringVar(value="")
                    radio_buttons = []
                    
                    # 创建主框架 - 避免使用CTkScrollableFrame以防止canvas未pack的问题
                    main_frame = ctk.CTkFrame(container)
                    main_frame.pack(fill='both', expand=True, pady=(0,8))
                    
                    # 创建一个画布用于滚动
                    canvas = tk.Canvas(main_frame, height=350, highlightthickness=0)
                    canvas.pack(side='left', fill='both', expand=True)
                    
                    # 添加滚动条
                    scrollbar = ctk.CTkScrollbar(main_frame, orientation='vertical', command=canvas.yview)
                    scrollbar.pack(side='right', fill='y')
                    
                    # 连接画布和滚动条
                    canvas.configure(yscrollcommand=scrollbar.set)
                    
                    # 创建内部框架作为画布的内容
                    inner_scroll = ctk.CTkFrame(canvas)
                    
                    # 将内部框架添加到画布
                    canvas_window = canvas.create_window((0, 0), window=inner_scroll, anchor='nw', width=canvas.winfo_width())
                    
                    # 绑定大小变化事件以更新滚动区域
                    def on_configure(event):
                        canvas.configure(scrollregion=canvas.bbox('all'))
                        # 确保内部框架宽度与画布一致
                        canvas.itemconfig(canvas_window, width=canvas.winfo_width())
                    
                    # 绑定事件
                    inner_scroll.bind('<Configure>', on_configure)
                    
                    # 确保所有组件正确初始化
                    main_frame.update_idletasks()
                    canvas.update_idletasks()
                    inner_scroll.update_idletasks()
                    
                except Exception as frame_error:
                    # 如果框架创建失败，使用更简单的后备方案
                    status_var.set(f"⚠️ UI初始化异常: {frame_error}")
                    # 创建一个简单的标签显示错误信息
                    error_label = ctk.CTkLabel(container, text=f"UI初始化失败: {frame_error}", text_color="red")
                    error_label.pack(fill='both', expand=True, pady=20)
                    return
                
                # 限制显示行数以提高性能
                display_rows = rows[:max_rows]
                if len(rows) > max_rows:
                    # 添加提示信息 - 使用inner_scroll作为父容器
                    info_frame = ctk.CTkFrame(inner_scroll)
                    info_frame.pack(fill='x', pady=(0,5))
                    ctk.CTkLabel(info_frame, text=f"ℹ️ 显示前 {max_rows} 个版本，共 {len(rows)} 个版本", 
                                text_color="gray", font=('', 9)).pack(side='left', padx=5)
                
                # 创建表头 - 放在内部滚动框架中
                header = ctk.CTkFrame(inner_scroll)
                header.pack(fill='x', pady=(0,2))  # 减少间距
                ctk.CTkLabel(header, text="🔢 版本ID", width=100).pack(side='left', padx=2)  # 减少宽度
                ctk.CTkLabel(header, text="📝 更新内容", width=300).pack(side='left', padx=2)  # 减少宽度
                ctk.CTkLabel(header, text="📅 日期", width=80).pack(side='left', padx=2)  # 减少宽度
                ctk.CTkLabel(header, text="🎯 选择", width=60).pack(side='left', padx=2)  # 减少宽度
                
                # 批量创建行，减少UI更新次数
                row_frames = []
                for i, (rid, msg, date, ref) in enumerate(display_rows):
                    row = ctk.CTkFrame(inner_scroll)  # 改为使用inner_scroll
                    row.pack(fill='x', pady=1)
                    row_frames.append(row)
                    
                    # 使用更简洁的标签
                    ctk.CTkLabel(row, text=rid[:8], width=100, anchor='w', font=('', 10)).pack(side='left', padx=2)  # 限制长度和字体大小
                    ctk.CTkLabel(row, text=msg[:40] + ('...' if len(msg) > 40 else ''), width=300, anchor='w', font=('', 10)).pack(side='left', padx=2)  # 截断长文本
                    ctk.CTkLabel(row, text=date, width=80, anchor='w', font=('', 10)).pack(side='left', padx=2)
                    
                    def make_radio_command(r, row_frame):
                        def on_select_radio():
                            # 禁用所有单选框避免重复点击
                            for rb in radio_buttons:
                                try:
                                    rb.configure(state='disabled')
                                except tk.TclError:
                                    # 忽略已销毁的widget错误
                                    continue
                            
                            # 异步执行版本切换
                            def async_switch():
                                try:
                                    # 定义统一的git执行函数
                                    def run_git_cmd(args):
                                        try:
                                            # 使用从comfy_dir_var获取的路径，确保与用户选择一致
                                            result = subprocess.run(['git','-C',self.comfy_dir_var.get()]+args, capture_output=True, text=True, errors='replace', timeout=30)
                                            return result
                                        except Exception as e:
                                            # 创建一个模拟的CompletedProcess对象
                                            class MockCompletedProcess:
                                                def __init__(self):
                                                    self.returncode = 1
                                                    self.stdout = ''
                                                    self.stderr = str(e)
                                            return MockCompletedProcess()
                                    
                                    self._enqueue_progress_show(0.1)
                                    status_var.set("🔧 正在切换版本，请稍候...")
                                    try:
                                        run_git_cmd(['stash'])
                                    except Exception:
                                        pass
                                    # 执行fetch获取最新代码
                                    self._text_enqueue(f"[版本维护] 正在执行git fetch --all")
                                    fetch_result = run_git_cmd(['fetch', '--all'])
                                    if fetch_result.returncode != 0:
                                        self._text_enqueue(f"[版本维护] ⚠️ git fetch失败，但将继续切换版本: {fetch_result.stderr}")
                                    
                                    # 清理未跟踪文件，避免checkout失败
                                    self._text_enqueue(f"[版本维护] 正在清理未跟踪文件")
                                    clean_result = run_git_cmd(['clean', '-fd'])
                                    if clean_result.returncode != 0:
                                        self._text_enqueue(f"[版本维护] ⚠️ 清理未跟踪文件失败: {clean_result.stderr}")
                                    
                                    # 执行checkout命令，使用--force参数
                                    self._text_enqueue(f"[版本维护] 正在执行git checkout {r} --force")
                                    rr = run_git_cmd(['checkout', r, '--force'])
                                    
                                    # 检查checkout是否成功
                                    if rr.returncode != 0:
                                        error_msg = (rr.stderr or '').strip()
                                        self._text_enqueue(f"[版本维护] 版本切换失败: {error_msg}")
                                        status_var.set("❌ 版本切换失败")
                                        # 重新启用单选框
                                        for rb in radio_buttons:
                                            try:
                                                rb.configure(state='normal')
                                            except tk.TclError:
                                                # 忽略已销毁的widget错误
                                                continue
                                        return
                                    else:
                                        # 执行git fetch获取最新代码，因为在分离头指针状态下无法直接执行git pull
                                        self._text_enqueue(f"[版本维护] 正在执行git fetch以获取最新代码")
                                        
                                        # 添加重试机制，处理网络连接问题
                                        max_retries = 3
                                        fetch_success = False
                                        
                                        for retry in range(max_retries):
                                            fetch_result = run_git_cmd(['fetch', '--all'])
                                            if fetch_result.returncode == 0:
                                                self._text_enqueue(f"[版本维护] ✅ git fetch成功，代码已更新")
                                                fetch_success = True
                                                break
                                            else:
                                                error_msg = f"{fetch_result.stdout or ''}{fetch_result.stderr or ''}"
                                                if retry < max_retries - 1:
                                                    self._text_enqueue(f"[版本维护] ⚠️ git fetch失败 (重试 {retry + 1}/{max_retries}): {error_msg}")
                                                    self._text_enqueue(f"[版本维护] 正在等待3秒后重试...")
                                                    time.sleep(3)
                                                else:
                                                    self._text_enqueue(f"[版本维护] ❌ git fetch失败 (已重试 {max_retries}次): {error_msg}")
                                                    self._text_enqueue(f"[版本维护] 可能是网络延迟或连接问题，将继续安装依赖")
                                                
                                        # 即使fetch失败，也继续安装依赖，因为checkout已经成功切换了版本
                                    
                                    # 版本切换成功，检测并安装依赖
                                    status_var.set("📦 版本切换完成，正在检测依赖...")
                                    self._text_enqueue(f"[版本维护] ✅ 版本切换至 {r}")
                                    self._text_enqueue(f"[版本维护] 当前工作目录: {self.comfy_dir_var.get()}")
                                    
                                    # 检测依赖文件 - 只安装ComfyUI根目录的requirements.txt文件
                                    requirements_files = []
                                    repo_path = repo
                                    
                                    # 只检测requirements.txt文件
                                    root_req = os.path.join(repo_path, 'requirements.txt')
                                    if os.path.isfile(root_req):
                                        requirements_files.append(root_req)
                                    
                                    if requirements_files:
                                        status_var.set(f"📦 发现 {len(requirements_files)} 个依赖文件，准备安装...")
                                        self._text_enqueue(f"[版本维护] 发现依赖文件: {len(requirements_files)} 个")
                                        
                                        # 获取当前Python环境
                                        python_exe = self.python_exe_path or os.path.join(os.getcwd(), 'python_embeded', 'python.exe')
                                        if not os.path.isfile(python_exe):
                                            python_exe = 'python'  # 回退到系统python
                                        
                                        # 安装依赖文件
                                        total_files = len(requirements_files)
                                        for i, req_file in enumerate(requirements_files, 1):
                                            try:
                                                status_var.set(f"📦 正在安装依赖 [{i}/{total_files}]: {os.path.basename(req_file)}")
                                                self._text_enqueue(f"[版本维护] 安装依赖文件: {req_file}")
                                                
                                                # 使用pip安装requirements.txt，显示详细安装过程
                                                self._text_enqueue(f"[版本维护] 正在安装依赖: {os.path.basename(req_file)}")
                                                cmd = [python_exe, '-m', 'pip', 'install', '-r', req_file]
                                                
                                                # 使用实时输出捕获，显示详细安装过程
                                                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
                                                
                                                output_lines = []
                                                while True:
                                                    try:
                                                        line = proc.stdout.readline()
                                                    except UnicodeDecodeError:
                                                        # 如果遇到编码错误，尝试跳过这一行
                                                        continue
                                                    if not line and proc.poll() is not None:
                                                        break
                                                    if line:
                                                        msg = line.strip()
                                                        output_lines.append(msg)
                                                        # 实时输出到结果面板
                                                        self._text_enqueue(f"[依赖安装] {msg}")
                                                
                                                returncode = proc.poll()
                                                
                                                if returncode == 0:
                                                    self._text_enqueue(f"[版本维护] ✅ 依赖安装成功: {os.path.basename(req_file)}")
                                                else:
                                                    self._text_enqueue(f"[版本维护] ⚠️ 依赖安装失败: {os.path.basename(req_file)} - 返回码: {returncode}")
                                            
                                            except subprocess.TimeoutExpired:
                                                self._text_enqueue(f"[版本维护] ⏰ 依赖安装超时: {os.path.basename(req_file)}")
                                            except Exception as e:
                                                self._text_enqueue(f"[版本维护] ❌ 依赖安装异常: {os.path.basename(req_file)} - {e}")
                                        
                                        status_var.set(f"✅ 依赖安装完成，共处理 {total_files} 个文件")
                                    else:
                                        status_var.set("✅ 版本切换完成，未找到ComfyUI依赖文件")
                                        self._text_enqueue("[版本维护] 未找到ComfyUI根目录的requirements.txt，跳过依赖安装")
                                        
                                except Exception as e:
                                    status_var.set(f"❌ 版本切换异常: {e}")
                                    self._text_enqueue(f"[版本维护] 版本切换异常: {e}")
                                finally:
                                    self._enqueue_progress(1.0)
                                    self._enqueue_progress_hide()
                                    # 重新启用单选框
                                    for rb in radio_buttons:
                                        try:
                                            rb.configure(state='normal')
                                        except tk.TclError:
                                            # 忽略已销毁的widget错误
                                            continue
                            
                            # 在新线程中执行异步切换
                            import threading
                            switch_thread = threading.Thread(target=async_switch, daemon=True)
                            switch_thread.start()
                        
                        return on_select_radio
                    
                    # 所有radio button共享同一个变量，确保单选行为
                    rb = ctk.CTkRadioButton(row, text='', variable=selected_var, value=ref, command=make_radio_command(ref, row))
                    rb.pack(side='left', padx=2)
                    radio_buttons.append(rb)
                    
                    is_current = False
                    if current_ref_is_tag:
                        is_current = (describe_var and ref in describe_var)  # 使用参数传入的describe_var
                    else:
                        is_current = (head.startswith(ref))
                    # 强制选中当前版本，无论之前是否有选择
                    if is_current:
                        try:
                            selected_var.set(ref)
                        except Exception:
                            pass

            status_var = tk.StringVar(value="⏳ 正在初始化版本管理界面...")
            info_label = ctk.CTkLabel(main, textvariable=status_var, anchor='w', justify='left')
            info_label.pack(fill='x', pady=(4,0), padx=2)
            
            # 异步加载版本列表
            def async_load_version_list():
                try:
                    # 获取远程数据并更新状态
                    status_var.set("🔄 正在获取远程标签信息...")
                    self._text_enqueue("[版本维护] 正在获取远程标签信息...")
                    
                    # 确保在正确的作用域内获取当前版本信息
                    def get_current_describe():
                        try:
                            return (subprocess.run(['git','-C',repo,'describe','--tags','--always'], 
                                                   capture_output=True, text=True, errors='replace', timeout=15).stdout or '').strip()
                        except Exception:
                            return ''  # 如果获取失败，使用空字符串
                    
                    describe = get_current_describe()  # 获取当前版本信息，用于后续比较
                    # 同时获取当前分支信息
                    try:
                        branch = (subprocess.run(['git','-C',repo,'rev-parse','--abbrev-ref','HEAD'], 
                                               capture_output=True, text=True, errors='replace').stdout or '').strip()
                        # 更新显示当前版本的变量
                        branch_var.set(f"📝 当前分支: {branch or '未知'}    🔖 当前版本: {describe or '未知'}")
                    except Exception:
                        pass
                    
                    # 使用fetch --all获取所有标签和分支，确保获取到最新的版本信息
                    try:
                        run_git(['fetch','--all','--tags','--timeout=30'])
                    except Exception:
                        # 如果带超时参数的fetch失败，使用普通fetch
                        try:
                            run_git(['fetch','--all','--tags'])
                        except Exception:
                            # 最后尝试只获取标签
                            run_git(['fetch','--tags'])
                    
                    # 获取稳定版数据
                    status_var.set("📋 正在获取稳定版版本列表...")
                    self._text_enqueue("[版本维护] 正在获取稳定版版本列表...")
                    
                    # 获取所有标签，不限制查询范围，确保获取到所有版本
                    tags_count_result = run_git(['tag','--list','--sort=-version:refname'])
                    all_tags = (tags_count_result.stdout or '').strip().splitlines()
                    
                    # 过滤出稳定版本标签，包括以v开头的标签和其他可能的稳定版本标签
                    stable_tags = []
                    for tag in all_tags:
                        # 同时接受带有后缀的版本标签（如0.3.75）
                        if tag.startswith('v') or re.match(r'^\d+\.\d+', tag) or tag:
                            stable_tags.append(tag)
                    
                    # 如果没有找到稳定版本标签，使用所有标签
                    if not stable_tags:
                        stable_tags = all_tags
                    
                    # 确保标签按版本号降序排序，最新版本在前
                    def version_key(tag):
                        # 移除v前缀
                        if tag.startswith('v'):
                            tag = tag[1:]
                        # 分割版本号，处理可能的后缀
                        parts = tag.split('.')
                        # 转换为整数元组，用于比较
                        try:
                            return tuple(int(part.split('-')[0]) for part in parts)
                        except ValueError:
                            # 如果无法转换，使用原始标签
                            return tuple(parts)
                    
                    stable_tags.sort(key=version_key, reverse=True)
                    
                    # 初始化stable_rows列表
                    stable_rows = []
                    
                    # 如果没有标签，获取最近的30个提交
                    if not stable_tags:
                        self._text_enqueue("[版本维护] ⚠️ 未找到标签，获取最近的30个提交")
                        # 获取最近的30个提交
                        log_result = run_git(['log','--oneline','--format=%h;%ad;%s','--date=short','-30'])
                        log_lines = (log_result.stdout or '').strip().splitlines()
                        for line in log_lines:
                            parts = line.split(';')
                            if len(parts) >= 3:
                                rid = parts[0].strip()
                                date = parts[1].strip()
                                msg = parts[2].strip()
                                stable_rows.append((rid, msg, date, rid))
                        # 直接返回，不再处理标签
                        def update_ui():
                            try:
                                # 确保table_container存在且可访问
                                if not hasattr(table_container, 'winfo_children'):
                                    raise Exception("表格容器不可用")
                                
                                # 清空现有内容
                                try:
                                    for widget in table_container.winfo_children():
                                        widget.destroy()
                                except Exception as destroy_error:
                                    self._text_enqueue(f"[版本维护] 清理旧UI组件失败: {destroy_error}")
                                
                                # 显式刷新容器状态
                                table_container.update_idletasks()
                                
                                # 构建新表格，显示提交历史
                                build_table(table_container, stable_rows, current_ref_is_tag=False, max_rows=30, describe_var=describe)
                                
                                # 显示实际的版本数量
                                status_var.set(f"✅ 版本列表已更新 (显示最近30个提交)")
                                self._text_enqueue(f"[版本维护] 版本列表加载完成，显示最近30个提交")
                            except Exception as e:
                                status_var.set(f"❌ UI更新失败: {e}")
                                self._text_enqueue(f"[版本维护] UI更新失败: {e}")
                        
                        # 延迟执行UI更新
                        dialog.after(300, lambda: self._ui_queue.put(('update_version_list', update_ui)))
                        return
                    
                    # 根据用户设置的显示数量处理标签
                    try:
                        display_count = max(1, min(100, display_count_var.get()))  # 限制在1-100之间
                    except Exception:
                        display_count = 30  # 默认值
                    tags = stable_tags[:display_count]
                    
                    # 批量获取提交信息，减少进程调用次数
                    for i, t in enumerate(tags):
                        try:
                            # 每5个标签更新一次状态，避免频繁UI更新
                            if i % 5 == 0:
                                status_var.set(f"📋 正在处理标签 {i+1}/{len(tags)}...")
                                self._text_enqueue(f"[版本维护] 正在处理标签 {i+1}/{len(tags)}...")
                            
                            info = (subprocess.run(['git','-C',repo,'show','-s','--format=%h;%ad;%s','--date=short',t], 
                                                  capture_output=True, text=True, errors='replace', timeout=15).stdout or '').strip()
                            parts = (info or '; ; ').split(';')
                            rid = parts[0].strip()
                            date = parts[1].strip()
                            msg = parts[2].strip()
                            stable_rows.append((rid, msg or t, date or '', t))
                        except subprocess.TimeoutExpired:
                            # 超时则使用简化信息
                            stable_rows.append((t[:8], t, '', t))
                        except Exception:
                            # 其他错误也使用简化信息
                            stable_rows.append((t[:8], t, '', t))
                    
                    # 在主线程中更新UI
                    def update_ui():
                        try:
                            # 确保table_container存在且可访问
                            if not hasattr(table_container, 'winfo_children'):
                                raise Exception("表格容器不可用")
                            
                            # 清空现有内容 - 添加错误处理
                            try:
                                for widget in table_container.winfo_children():
                                    widget.destroy()
                            except Exception as destroy_error:
                                self._text_enqueue(f"[版本维护] 清理旧UI组件失败: {destroy_error}")
                            
                            # 显式刷新容器状态
                            table_container.update_idletasks()
                            
                            # 构建新表格，限制显示数量，传入describe变量
                            # 使用用户设置的显示数量
                            try:
                                display_count = max(1, min(100, display_count_var.get()))  # 限制在1-100之间
                            except Exception:
                                display_count = 30  # 默认值
                            build_table(table_container, stable_rows, current_ref_is_tag=True, max_rows=display_count, describe_var=describe)
                            
                            # 再次刷新以确保所有组件正确渲染
                            table_container.update_idletasks()
                            
                            # 显示实际的版本数量
                            try:
                                display_count = max(1, min(100, display_count_var.get()))  # 限制在1-100之间
                            except Exception:
                                display_count = 30  # 默认值
                            status_var.set(f"✅ 版本列表已更新 (显示前{display_count}个，共{len(stable_tags)}个稳定版本)")
                            self._text_enqueue(f"[版本维护] 版本列表加载完成，共{len(stable_tags)}个稳定版本标签")
                        except Exception as e:
                            status_var.set(f"❌ UI更新失败: {e}")
                            self._text_enqueue(f"[版本维护] UI更新失败: {e}")
                    
                    # 延迟执行UI更新，确保对话框完全渲染
                    dialog.after(300, lambda: self._ui_queue.put(('update_version_list', update_ui)))
                    
                except Exception as e:
                    def update_error():
                        status_var.set(f"❌ 获取版本列表失败: {e}")
                    # 延迟执行错误处理
                    dialog.after(300, lambda: self._ui_queue.put(('update_error', update_error)))
                    self._text_enqueue(f"[版本维护] 获取版本列表失败: {e}")
            
            # 在新线程中加载版本列表
            import threading
            load_thread = threading.Thread(target=async_load_version_list, daemon=True)
            load_thread.start()

            def refresh_version_list():
                """刷新版本列表数据"""
                try:
                    status_var.set("🔄 正在刷新版本列表，请稍候...")
                    
                    # 禁用刷新按钮避免重复点击
                    refresh_btn.configure(state='disabled')
                    
                    # 确保在正确的作用域内获取当前版本信息
                    def get_current_describe():
                        try:
                            return (subprocess.run(['git','-C',repo,'describe','--tags','--always'], 
                                                   capture_output=True, text=True, errors='replace', timeout=15).stdout or '').strip()
                        except Exception:
                            return ''  # 如果获取失败，使用空字符串
                    
                    describe = get_current_describe()  # 获取当前版本信息，用于后续比较
                    
                    # 使用fetch --all获取所有标签和分支，确保获取到最新的版本信息
                    try:
                        run_git(['fetch','--all','--tags','--timeout=30'])
                    except Exception:
                        # 如果带超时参数的fetch失败，使用普通fetch
                        try:
                            run_git(['fetch','--all','--tags'])
                        except Exception:
                            # 最后尝试只获取标签
                            run_git(['fetch','--tags'])
                    
                    # 获取所有标签，不限制查询范围，确保获取到所有版本
                    tags_count_result = run_git(['tag','--list','--sort=-version:refname'])
                    all_tags = (tags_count_result.stdout or '').strip().splitlines()
                    
                    # 过滤出稳定版本标签，包括以v开头的标签和其他可能的稳定版本标签
                    stable_tags = []
                    for tag in all_tags:
                        # 接受以v开头的标签（如v1.0.0）和纯数字版本标签（如1.0.0）
                        # 同时接受带有后缀的版本标签（如0.3.75）
                        if tag.startswith('v') or re.match(r'^\d+\.\d+', tag):
                            stable_tags.append(tag)
                    
                    # 如果没有找到稳定版本标签，使用所有标签
                    if not stable_tags:
                        stable_tags = all_tags
                    
                    # 确保标签按版本号降序排序，最新版本在前
                    def version_key(tag):
                        # 移除v前缀
                        if tag.startswith('v'):
                            tag = tag[1:]
                        # 分割版本号，处理可能的后缀
                        parts = tag.split('.')
                        # 转换为整数元组，用于比较
                        try:
                            return tuple(int(part.split('-')[0]) for part in parts)
                        except ValueError:
                            # 如果无法转换，使用原始标签
                            return tuple(parts)
                    
                    stable_tags.sort(key=version_key, reverse=True)
                    
                    # 根据用户设置的显示数量处理标签
                    try:
                        display_count = max(1, min(100, display_count_var.get()))  # 限制在1-100之间
                    except Exception:
                        display_count = 30  # 默认值
                    tags = stable_tags[:display_count]
                    stable_rows = []
                    
                    # 批量获取提交信息，减少进程调用次数
                    for i, t in enumerate(tags):
                        try:
                            # 每5个标签更新一次状态，避免频繁UI更新
                            if i % 5 == 0:
                                status_var.set(f"📋 正在刷新标签 {i+1}/{len(tags)}...")
                            
                            info = (subprocess.run(['git','-C',repo,'show','-s','--format=%h;%ad;%s','--date=short',t], 
                                                  capture_output=True, text=True, errors='replace', timeout=15).stdout or '').strip()
                            parts = (info or '; ; ').split(';')
                            rid = parts[0].strip()
                            date = parts[1].strip()
                            msg = parts[2].strip()
                            stable_rows.append((rid, msg or t, date or '', t))
                        except subprocess.TimeoutExpired:
                            # 超时则使用简化信息
                            stable_rows.append((t[:8], t, '', t))
                        except Exception:
                            # 其他错误也使用简化信息
                            stable_rows.append((t[:8], t, '', t))
                    
                    # 延迟执行UI更新，避免阻塞
                    def update_table():
                        try:
                            # 清空并重新构建稳定版表格，传入describe变量
                            for widget in table_container.winfo_children():
                                widget.destroy()
                            # 使用用户设置的显示数量
                            try:
                                display_count = max(1, min(100, display_count_var.get()))  # 限制在1-100之间
                            except Exception:
                                display_count = 30  # 默认值
                            build_table(table_container, stable_rows, current_ref_is_tag=True, max_rows=display_count, describe_var=describe)
                            
                            # 显示实际的版本数量
                            try:
                                display_count = max(1, min(100, display_count_var.get()))  # 限制在1-100之间
                            except Exception:
                                display_count = 30  # 默认值
                            status_var.set(f"✅ 版本列表已刷新 (显示前{display_count}个，共{len(all_tags)}个)")
                            self._text_enqueue(f"[版本维护] 版本列表刷新完成，共{len(all_tags)}个标签")
                        except Exception as e:
                            status_var.set(f"❌ UI更新失败: {e}")
                            self._text_enqueue(f"[版本维护] UI更新失败: {e}")
                        finally:
                            # 重新启用刷新按钮
                            refresh_btn.configure(state='normal')
                    
                    # 使用延迟执行避免UI阻塞
                    dialog.after(200, update_table)
                    
                except Exception as e:
                    status_var.set(f"❌ 刷新失败: {e}")
                    self._text_enqueue(f"[版本维护] 刷新版本列表失败: {e}")
                    # 确保刷新按钮重新启用
                    refresh_btn.configure(state='normal')

            # 创建显示数量控制变量，默认30
            display_count_var = tk.IntVar(value=30)
            
            # 创建底部按钮区域
            btns = ctk.CTkFrame(main)
            btns.pack(fill='x', pady=8)
            ctk.CTkLabel(btns, text="💡 提示：选择单选项将立即切换版本并自动安装依赖，过程可能因网络延迟稍有等待。", text_color="white").pack(side='left', padx=6)
            
            # 创建显示数量控制区域
            count_frame = ctk.CTkFrame(btns)
            count_frame.pack(side='right', padx=4)
            ctk.CTkLabel(count_frame, text="显示数量:", width=60).pack(side='left', padx=2)
            count_entry = ctk.CTkEntry(count_frame, textvariable=display_count_var, width=60, justify='center')
            count_entry.pack(side='left', padx=2)
            
            # 刷新和关闭按钮
            refresh_btn = ctk.CTkButton(btns, text="🔄 刷新", width=80, command=refresh_version_list)
            refresh_btn.pack(side='right', padx=4)
            ctk.CTkButton(btns, text="关闭", width=90, command=dialog.destroy).pack(side='right', padx=6)
            
            # 延迟自动刷新，确保对话框完全显示
            dialog.after(1000, lambda: refresh_version_list() if dialog.winfo_exists() else None)
        except Exception as e:
            self.update_result_text(f"[版本维护] 异常: {e}")

    def _switch_comfy_version(self, mode: str):
        try:
            self._enqueue_progress_show(0.05)
            update_dir = os.path.join(os.getcwd(), 'update')
            # 使用comfy_dir_var获取用户选择的ComfyUI路径，而不是硬编码路径
            comfy_dir = self.comfy_dir_var.get()
            # 如果comfy_dir_var为空或无效，使用默认路径作为备选
            if not comfy_dir or not os.path.isdir(comfy_dir):
                comfy_dir = os.path.join(os.getcwd(), 'ComfyUI')
            py_embed = os.path.join(os.getcwd(), 'python_embeded', 'python.exe')

            if not os.path.isfile(py_embed):
                self._show_dark_warning("⚠️ 环境缺失", "未找到便携版的 python_embeded\\python.exe", f"路径: {py_embed}\n请确保便携版目录结构完整")
                self._enqueue_progress_hide()
                return
            if not os.path.isdir(update_dir) or not os.path.isfile(os.path.join(update_dir, 'update.py')):
                self._show_dark_warning("⚠️ 更新脚本缺失", "未找到 update\\update.py", f"路径: {update_dir}\n请确认更新脚本已复制到项目 update 目录")
                self._enqueue_progress_hide()
                return
            if not os.path.isdir(comfy_dir):
                self._show_dark_warning("⚠️ 目录缺失", "未找到 ComfyUI 目录", f"路径: {comfy_dir}\n请确认便携版 ComfyUI 目录存在")
                self._enqueue_progress_hide()
                return

            args = [py_embed, os.path.join(update_dir, 'update.py'), comfy_dir]
            if str(mode).lower() == 'stable':
                args.append('--stable')

            self._text_enqueue(f"[版本维护] 🚀 启动更新：{('稳定版' if mode=='stable' else '开发版')}\npython_embeded: {py_embed}\nupdate.py: {os.path.join(update_dir, 'update.py')}\nComfyUI: {comfy_dir}")

            def run_once(skip_self=False):
                cmd = list(args)
                if skip_self:
                    cmd.append('--skip_self_update')
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
                while True:
                    try:
                        line = proc.stdout.readline()
                    except UnicodeDecodeError:
                        # 如果遇到编码错误，尝试跳过这一行
                        continue
                    if not line and proc.poll() is not None:
                        break
                    if line:
                        self._text_enqueue(line.strip())
                return proc.poll()

            def _task():
                try:
                    rc = run_once(False)
                    self._enqueue_progress(0.6)
                    upd_new = os.path.join(update_dir, 'update_new.py')
                    upd_py = os.path.join(update_dir, 'update.py')
                    if os.path.isfile(upd_new):
                        try:
                            shutil.move(upd_new, upd_py)
                            self._text_enqueue("[版本维护] 🔄 检测到更新脚本，已替换为最新版本，准备再次运行")
                        except Exception as e:
                            self._text_enqueue(f"[版本维护] 替换更新脚本失败: {e}")
                        rc = run_once(True)
                    self._enqueue_progress(0.9)
                    if rc == 0:
                        self._text_enqueue("[版本维护] ✅ 版本切换完成")
                        try:
                            self.after(150, self._refresh_current_version_label)
                        except Exception:
                            pass
                    else:
                        self._text_enqueue(f"[版本维护] ❌ 更新返回码: {rc}")
                except Exception as e:
                    self._text_enqueue(f"[版本维护] 异常: {e}")
                finally:
                    self._enqueue_progress(1.0)
                    self._enqueue_progress_hide()

            Thread(target=_task, daemon=True).start()
        except Exception as e:
            self.update_result_text(f"[版本维护] 启动失败: {e}")


if __name__ == '__main__':
    app = ComfyUIEnvironmentManager()
    app.mainloop()
