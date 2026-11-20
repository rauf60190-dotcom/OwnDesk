"""Windows service installation and maintenance updates without blocking Tk."""
import os
from pathlib import Path
import queue
import re
import threading
import tkinter as tk
from tkinter import ttk, simpledialog, filedialog, messagebox
import webbrowser

import windows_service as service
import windows_update_ui as updater
import windows_recovery_ui as recovery
import windows_uninstall_ui as removal
import resource_requirements

BG, PANEL, TEXT, MUTED, FIELD = '#0b1220', '#111d30', '#f0f5ff', '#a8b9d2', '#08101d'


class InstallerWindow:
    def __init__(self, window, preview=False, update_tab=False, update_runner=updater.run, recovery_runner=recovery.run, uninstall_runner=removal.run):
        self.window, self.preview, self.update_runner = window, preview, update_runner
        self.events, self.busy, self.target = queue.Queue(), False, None
        self.checked = updater.CheckedUpdate()
        self.checked_backup = recovery.CheckedBackup()
        self.recovery_runner = recovery_runner
        self.uninstall_runner = uninstall_runner
        self.removal_checked = None
        self.removal_confirmation = tk.StringVar(window)
        self.removal_summary = tk.StringVar(window, value='Сначала проверьте установленную службу организации.')
        self.backup_directory = tk.StringVar(window)
        self.backup_summary = tk.StringVar(window, value='Выберите завершённую копию и проверьте её перед восстановлением.')
        self.recovery_output = tk.StringVar(window)
        self.wrap_labels = []
        self.app, self.data = service.paths()
        self.archive, self.checksum, self.transaction = (tk.StringVar(window) for _ in range(3))
        self.selected = tk.StringVar(window, value='Организация')
        self.last_service = self.selected.get()
        self.detail = tk.StringVar(window, value='Установите новую организацию или проверьте обновление существующей службы. Данные хранятся на этом компьютере.')
        self.summary = tk.StringVar(window, value='Сначала выберите архив и проверьте совместимость.')
        window.title('OwnDesk — рабочий сервер')
        window.geometry('980x780')
        window.minsize(900, 740)
        window.configure(bg=BG)
        style = ttk.Style(window)
        style.theme_use('clam')
        style.configure('OwnDesk.TNotebook', background=PANEL, borderwidth=0)
        style.configure('OwnDesk.TNotebook.Tab', background='#192941', foreground=MUTED, padding=(16, 9), font=('Segoe UI', 10))
        style.map('OwnDesk.TNotebook.Tab', background=[('selected', '#29446b')], foreground=[('selected', TEXT)])
        style.configure('OwnDesk.TCombobox', fieldbackground=FIELD, background='#24344f', foreground=TEXT, arrowcolor=TEXT)
        style.map('OwnDesk.TCombobox', fieldbackground=[('readonly', FIELD)], foreground=[('readonly', TEXT)])
        style.configure('OwnDesk.Horizontal.TProgressbar', troughcolor=FIELD, background='#3766d6',
                        bordercolor=PANEL, lightcolor='#3766d6', darkcolor='#3766d6')
        frame = tk.Frame(window, bg=PANEL, padx=24, pady=18)
        frame.pack(expand=True, fill='both', padx=16, pady=16)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        header = tk.Frame(frame, bg=PANEL)
        header.grid(row=0, column=0, sticky='ew')
        self.label(header, 'OwnDesk Server', font=('Segoe UI', 24, 'bold')).pack(side='left')
        release = (Path(__file__).resolve().parents[1] / 'VERSION').read_text().strip()
        self.label(header, 'Установщик ' + release, fg=MUTED).pack(side='right')
        self.label(frame, variable=self.detail, fg=MUTED, wraplength=880).grid(row=1, column=0, sticky='ew', pady=(8, 14))
        self.tabs = ttk.Notebook(frame, style='OwnDesk.TNotebook')
        self.tabs.grid(row=2, column=0, sticky='nsew')
        install_page, update_page = (tk.Frame(self.tabs, bg=PANEL, padx=12, pady=12) for _ in range(2))
        self.tabs.add(install_page, text='Установка организации')
        self.tabs.add(update_page, text='Обновление и возврат')
        self.build_install(install_page)
        self.build_update(update_page)
        restore_page = tk.Frame(self.tabs, bg=PANEL, padx=12, pady=12)
        self.tabs.add(restore_page, text='Восстановление данных')
        self.build_restore(restore_page)
        removal_page = tk.Frame(self.tabs, bg=PANEL, padx=12, pady=12)
        self.tabs.add(removal_page, text='Удаление службы')
        self.build_removal(removal_page)
        self.progress = ttk.Progressbar(frame, mode='indeterminate', style='OwnDesk.Horizontal.TProgressbar')
        self.progress.grid(row=3, column=0, sticky='ew', pady=(12, 8))
        self.log = tk.Text(frame, height=6, bg=FIELD, fg=MUTED, font=('Consolas', 9), relief='flat', state='disabled', wrap='word')
        self.log.grid(row=4, column=0, sticky='ew')
        for variable in (self.archive, self.checksum, self.selected): variable.trace_add('write', self.invalidate)
        self.transaction.trace_add('write', lambda *_: self.refresh())
        self.backup_directory.trace_add('write', self.invalidate_backup)
        self.removal_confirmation.trace_add('write', lambda *_: self.refresh())
        if preview: self.detail.set('Предпросмотр: установка, обновление, журналы и запуск служб отключены.')
        if update_tab or (not preview and (self.app / 'OwnDeskService.xml').exists()): self.tabs.select(update_page)
        self.refresh()
        window.bind('<Configure>', self.resize)
        window.protocol('WM_DELETE_WINDOW', self.close)
        self.tick_id = window.after(200, self.tick)

    def label(self, parent, text='', variable=None, fg=TEXT, **kwargs):
        label = tk.Label(parent, text=text, textvariable=variable, bg=PANEL, fg=fg, justify='left', anchor='w', **kwargs)
        if 'wraplength' in kwargs: self.wrap_labels.append(label)
        return label

    def resize(self, event):
        if event.widget == self.window:
            for label in self.wrap_labels: label.configure(wraplength=max(480, event.width - 140))

    def button(self, parent, text, command, primary=False):
        button = tk.Button(parent, text=text, command=command, bg='#3766d6' if primary else '#24344f', fg='white',
            disabledforeground=MUTED, activebackground='#34558b', activeforeground='white', relief='flat', padx=13, pady=9, font=('Segoe UI', 10))
        button.primary = primary
        return button

    def entry(self, parent, variable):
        return tk.Entry(parent, textvariable=variable, bg=FIELD, fg=TEXT, insertbackground=TEXT,
            disabledbackground='#162136', disabledforeground=MUTED, relief='flat', font=('Segoe UI', 10))

    def build_install(self, page):
        self.label(page, 'Новая организация • PostgreSQL • защищённый адрес', font=('Segoe UI', 13, 'bold')).pack(anchor='w', pady=(8, 14))
        self.label(page, 'Код подключения потребуется после подготовки компонентов.\nПолучите свежий код в разделе «Мои установки» на owndesk.work.', fg=MUTED, wraplength=810).pack(anchor='w', pady=(0, 18))
        self.label(page, 'Служба будет работать после закрытия окна. Для первого запуска нужны интернет и несколько минут.', fg=MUTED, wraplength=810).pack(anchor='w', pady=(0, 18))
        self.label(page, resource_requirements.RECOMMENDATION, font=('Segoe UI', 10, 'bold'), wraplength=810).pack(anchor='w', pady=(0, 6))
        resource_summary, resource_advice = resource_requirements.describe(resource_requirements.collect())
        self.label(page, resource_summary, fg=MUTED, wraplength=810).pack(anchor='w', pady=(0, 4))
        self.label(page, resource_advice + ' Вложения и резервные копии требуют дополнительного места.', fg=MUTED,
                   font=('Segoe UI', 9), wraplength=810).pack(anchor='w', pady=(0, 14))
        actions = tk.Frame(page, bg=PANEL)
        actions.pack(fill='x')
        self.install = self.button(actions, 'Установить и запустить', lambda: self.start_install('install'), primary=True)
        self.install.pack(side='left', padx=(0, 8))
        self.open_button = self.button(actions, 'Открыть организацию', self.open_organization)
        self.open_button.pack(side='left', padx=(0, 8))
        self.install_logs = self.button(actions, 'Журналы', lambda: self.open_logs('OwnDeskServer'))
        self.install_logs.pack(side='left')

    def build_removal(self, page):
        self.label(page, 'Отключить сервер организации на этом компьютере', font=('Segoe UI', 13, 'bold')).pack(anchor='w', pady=(8, 12))
        self.label(page, 'Будет остановлена и удалена из Windows только служба OwnDeskServer. '
            'Сайт организации станет недоступен с этого компьютера. Центральный портал не удаляется.',
            fg=MUTED, wraplength=820).pack(anchor='w', fill='x', pady=(0, 12))
        self.label(page, 'База, вложения, ключи, настройки и файлы программы останутся на диске. '
            'Для работающей службы сначала создаётся резервная копия. Для уже остановленной службы '
            'свежая копия автоматически не создаётся: сохраните завершённую копию перед удалением.',
            fg=MUTED, wraplength=820).pack(anchor='w', fill='x', pady=(0, 12))
        self.label(page, variable=self.removal_summary, wraplength=820).pack(anchor='w', fill='x', pady=(0, 12))
        self.removal_check = self.button(page, 'Проверить службу организации', lambda: self.start_removal('check'))
        self.removal_check.pack(anchor='w', pady=(0, 12))
        self.label(page, 'Для подтверждения введите OwnDeskServer').pack(anchor='w')
        self.removal_entry = self.entry(page, self.removal_confirmation)
        self.removal_entry.pack(fill='x', ipady=7, pady=(4, 12))
        self.removal_apply = self.button(page, 'Удалить службу, сохранив данные', lambda: self.start_removal('remove'), primary=True)
        self.removal_apply.pack(anchor='w')

    def start_removal(self, action):
        if self.busy or self.preview: return
        token = ''
        confirmation = self.removal_confirmation.get().strip()
        if action == 'remove':
            if not self.removal_checked or confirmation != 'OwnDeskServer':
                self.detail.set('Проверьте службу и введите OwnDeskServer.'); return
            token = self.removal_checked['check_sha256']
            if not messagebox.askyesno('Удалить службу организации?',
                'OwnDeskServer будет остановлена и удалена из списка служб Windows. '
                'Организация станет недоступна. База, файлы, ключи и программа сохранятся.', parent=self.window): return
        self.launch('uninstall-check' if action == 'check' else 'uninstall-remove',
            lambda: self.uninstall_runner(action, self.report, token, confirmation))

    def build_update(self, page):
        page.columnconfigure(1, weight=1)
        self.label(page, 'Что обновляем').grid(row=0, column=0, sticky='w', padx=(0, 12))
        self.service_box = ttk.Combobox(page, textvariable=self.selected, values=list(updater.SERVICES), state='readonly', style='OwnDesk.TCombobox', font=('Segoe UI', 10))
        self.service_box.grid(row=0, column=1, sticky='ew', pady=(0, 8))
        self.label(page, 'Архив OwnDesk (.zip)').grid(row=1, column=0, columnspan=2, sticky='w')
        row = tk.Frame(page, bg=PANEL)
        row.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(4, 8))
        row.columnconfigure(0, weight=1)
        self.archive_entry = self.entry(row, self.archive)
        self.archive_entry.grid(row=0, column=0, sticky='ew', ipady=7)
        self.browse = self.button(row, 'Выбрать…', self.choose_archive)
        self.browse.grid(row=0, column=1, padx=(8, 0))
        self.label(page, 'Контрольная сумма SHA-256 из кабинета').grid(row=3, column=0, columnspan=2, sticky='w')
        self.sha_entry = self.entry(page, self.checksum)
        self.sha_entry.grid(row=4, column=0, columnspan=2, sticky='ew', ipady=7, pady=(4, 4))
        self.label(page, 'Скопируйте 64 символа рядом с дистрибутивом на owndesk.work.', fg=MUTED).grid(row=5, column=0, columnspan=2, sticky='w')
        actions = tk.Frame(page, bg=PANEL)
        actions.grid(row=6, column=0, columnspan=2, sticky='w', pady=(12, 8))
        self.check_button = self.button(actions, 'Проверить архив', lambda: self.start_update('check'))
        self.check_button.pack(side='left', padx=(0, 8))
        self.apply_button = self.button(actions, 'Обновить службу', lambda: self.start_update('apply'), primary=True)
        self.apply_button.pack(side='left', padx=(0, 8))
        self.update_logs = self.button(actions, 'Журналы', lambda: self.open_logs(self.selection().service))
        self.update_logs.pack(side='left')
        self.label(page, variable=self.summary, fg=MUTED, wraplength=820).grid(row=7, column=0, columnspan=2, sticky='ew')
        ttk.Separator(page).grid(row=8, column=0, columnspan=2, sticky='ew', pady=(12, 10))
        self.label(page, 'Если предыдущее обновление было прервано', font=('Segoe UI', 10, 'bold')).grid(row=9, column=0, columnspan=2, sticky='w')
        recovery = tk.Frame(page, bg=PANEL)
        recovery.grid(row=10, column=0, columnspan=2, sticky='ew', pady=(5, 4))
        recovery.columnconfigure(0, weight=1)
        self.recovery_entry = self.entry(recovery, self.transaction)
        self.recovery_entry.grid(row=0, column=0, sticky='ew', ipady=7)
        self.recover_button = self.button(recovery, 'Восстановить запуск', lambda: self.start_update('recover'))
        self.recover_button.grid(row=0, column=1, padx=(8, 0))
        self.label(page, 'Идентификатор операции из журнала: 32 символа. Возвращается программа с текущей базой.', fg=MUTED, wraplength=820).grid(row=11, column=0, columnspan=2, sticky='w')

    def selection(self):
        return updater.Selection(updater.SERVICES[self.selected.get()], self.archive.get().strip(), self.checksum.get().strip())

    def build_restore(self, page):
        self.label(page, 'Восстановление в отдельный закрытый стенд', font=('Segoe UI', 13, 'bold')).pack(anchor='w', pady=(6, 10))
        self.label(page, 'Рабочая установка не заменяется. Telegram, почта и публичный туннель не запускаются.', fg=MUTED, wraplength=820).pack(anchor='w', pady=(0, 16))
        self.label(page, 'Каталог резервной копии с manifest.json').pack(anchor='w')
        row = tk.Frame(page, bg=PANEL)
        row.pack(fill='x', pady=(4, 12))
        row.columnconfigure(0, weight=1)
        self.backup_entry = self.entry(row, self.backup_directory)
        self.backup_entry.grid(row=0, column=0, sticky='ew', ipady=7)
        self.backup_browse = self.button(row, 'Выбрать…', self.choose_backup)
        self.backup_browse.grid(row=0, column=1, padx=(8, 0))
        actions = tk.Frame(page, bg=PANEL)
        actions.pack(fill='x', pady=(0, 12))
        self.backup_check = self.button(actions, 'Проверить копию', lambda: self.start_restore('verify'))
        self.backup_check.pack(side='left', padx=(0, 8))
        self.backup_restore = self.button(actions, 'Восстановить отдельно', lambda: self.start_restore('recover'), primary=True)
        self.backup_restore.pack(side='left')
        self.label(page, variable=self.backup_summary, fg=MUTED, wraplength=820).pack(anchor='w', fill='x')
        ttk.Separator(page).pack(fill='x', pady=(16, 12))
        self.label(page, 'Будут сверены база, приватные файлы и ключи. Для проверки создаётся временная служба; '
            'после результата она удаляется, а восстановленная база остаётся остановленной. Если среды ещё нет, '
            'установщик подготовит её — потребуется интернет.', fg=MUTED, wraplength=820).pack(anchor='w', fill='x', pady=(0, 12))
        self.label(page, 'Каталог результата (можно скопировать путь)').pack(anchor='w')
        self.output_entry = self.entry(page, self.recovery_output)
        self.output_entry.configure(state='readonly', readonlybackground=FIELD)
        self.output_entry.pack(fill='x', ipady=7, pady=(4, 0))

    def invalidate_backup(self, *_):
        self.checked_backup.clear()
        self.recovery_output.set('')
        self.backup_summary.set('Выберите завершённую копию и проверьте её перед восстановлением.')
        self.refresh()

    def choose_backup(self):
        if self.busy or self.preview: return
        name = filedialog.askdirectory(parent=self.window, title='Закрытый каталог резервной копии OwnDesk',
            initialdir=str(Path(os.environ['ProgramData']) / 'OwnDesk/Backups'))
        if name: self.backup_directory.set(name)

    def start_restore(self, action):
        if self.busy or self.preview: return
        directory = self.backup_directory.get().strip()
        if not directory:
            self.detail.set('Выберите каталог резервной копии.')
            return
        seal = ''
        if action == 'recover':
            if not self.checked_backup.matches(directory):
                self.detail.set('Сначала проверьте выбранную копию.')
                return
            seal = self.checked_backup.result['manifest_sha256']
        self.launch('backup-verify' if action == 'verify' else 'restore-data',
            lambda: self.recovery_runner(directory, action, self.report, seal), directory)

    def invalidate(self, *_):
        if self.last_service != self.selected.get():
            self.last_service = self.selected.get()
            self.transaction.set('')
        self.checked.clear()
        self.summary.set('Сначала выберите архив и проверьте совместимость.')
        self.refresh()

    def refresh(self):
        enabled = not self.busy and not self.preview
        def toggle(button, active):
            button.configure(state='normal' if active else 'disabled', bg='#3766d6' if active and button.primary else '#24344f')
        for button in (self.install, self.open_button, self.install_logs, self.browse, self.check_button, self.update_logs): toggle(button, enabled)
        for button in (self.backup_browse, self.backup_check): toggle(button, enabled)
        toggle(self.removal_check, enabled)
        toggle(self.removal_apply, enabled and self.removal_checked is not None and self.removal_confirmation.get().strip() == 'OwnDeskServer')
        self.removal_entry.configure(state='disabled' if self.busy else 'normal')
        toggle(self.backup_restore, enabled and self.checked_backup.matches(self.backup_directory.get().strip()))
        toggle(self.apply_button, enabled and self.checked.matches(self.selection()))
        toggle(self.recover_button, enabled and re.fullmatch(r'[a-f0-9]{32}', self.transaction.get().strip()))
        for entry in (self.archive_entry, self.sha_entry, self.recovery_entry, self.backup_entry): entry.configure(state='disabled' if self.busy else 'normal')
        self.service_box.configure(state='disabled' if self.busy else 'readonly')

    def choose_archive(self):
        if self.busy or self.preview: return
        name = filedialog.askopenfilename(parent=self.window, title='Архив OwnDesk', filetypes=[('OwnDesk ZIP', '*.zip')])
        if name: self.archive.set(name); self.checksum.set('')

    def report(self, line): self.events.put(('line', line))

    def request_code(self):
        ready, answer = threading.Event(), {}
        self.events.put(('request-code', (ready, answer)))
        ready.wait()
        return answer.get('code')

    def launch(self, action, operation, selection=None):
        if self.busy or self.preview: return
        self.busy = True
        self.refresh()
        self.progress.start(15)
        self.detail.set('Выполняем операцию. Дождитесь результата перед закрытием окна.')
        def job():
            try: self.events.put(('done', (action, selection, operation())))
            except Exception as error: self.events.put(('error', error))
        threading.Thread(target=job, daemon=True).start()

    def start_install(self, action):
        if action == 'install': self.launch(action, lambda: service.install(report=self.report, code_provider=self.request_code))
        else: self.launch('open', lambda: service.wait_ready(self.app, self.data, 10))

    def open_organization(self):
        if self.preview or self.busy: return
        if self.target: webbrowser.open(self.target)
        else: self.start_install('open')

    def start_update(self, action):
        if self.busy or self.preview: return
        selected, transaction = self.selection(), self.transaction.get().strip()
        try:
            updater.command(selected, action, transaction)
            if action == 'apply':
                if not self.checked.matches(selected): raise ValueError('Сначала проверьте выбранный архив и службу.')
                info = self.checked.result
                if not messagebox.askyesno('Обновить службу OwnDesk?',
                    f"{self.selected.get()}: {info.get('installed')} → {info.get('candidate')}\n\n"
                    'Перед переключением создаётся резервная копия. Сайт временно станет недоступен. '
                    'При ошибке вернётся прежняя программа с текущей базой.', parent=self.window): return
            elif action == 'recover':
                if not messagebox.askyesno('Восстановить запуск?', self.selected.get() +
                    '\n\nБудет возвращена прежняя программа по журналу операции. Рабочая база не заменяется резервной копией. '
                    'Возможен короткий перерыв в работе сайта.', parent=self.window): return
        except ValueError as error: self.detail.set(str(error)); return
        self.launch(action, lambda: self.update_runner(selected, action, self.report, transaction), selected)

    def open_logs(self, name):
        if self.preview or self.busy: return
        _, data = updater.paths(name)
        path = data / 'service-logs'
        if not path.exists(): path = data / 'setup.log'
        if path.exists(): os.startfile(str(path))
        else: self.detail.set('Журналы выбранной службы пока не созданы.')

    def append(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', text + '\n')
        if int(self.log.index('end-1c').split('.')[0]) > 400: self.log.delete('1.0', '100.0')
        self.log.see('end')
        self.log.configure(state='disabled')

    def tick(self):
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind == 'request-code':
                ready, answer = value
                code = simpledialog.askstring('Подключение OwnDesk', 'Свежий код из «Мои установки» на owndesk.work. Для повтора можно оставить поле пустым:', show='•', parent=self.window)
                answer['code'] = code.strip() if code is not None else None
                ready.set()
            elif kind == 'line': self.append(value); self.detail.set(value)
            else:
                self.busy = False
                self.progress.stop()
                if kind == 'error':
                    self.checked.clear()
                    self.checked_backup.clear()
                    self.removal_checked = None
                    self.removal_summary.set('Операция не подтверждена. Повторите проверку службы.')
                    self.backup_summary.set('Восстановление не подтверждено. Проверьте сообщение и повторно проверьте копию.')
                    self.detail.set(str(value))
                    self.append(str(value))
                    if isinstance(value, updater.UpdateError):
                        if value.details: self.append(value.details)
                        if value.transaction: self.transaction.set(value.transaction)
                    self.summary.set('Операция не подтверждена. Проверьте сообщение и журнал перед повтором.')
                else:
                    action, selected, result = value
                    if action in {'install', 'open'}:
                        self.target = result
                        self.detail.set('Сервер готов. Адрес открыт в браузере; служба работает после закрытия окна.')
                        webbrowser.open(result)
                    elif action == 'check':
                        if selected.signature() == self.selection().signature():
                            try:
                                self.checked.accept(selected, result)
                            except ValueError as error:
                                self.checked.clear()
                                self.detail.set(str(error))
                                self.summary.set('Повторите проверку архива. Обновление пока недоступно.')
                            else:
                                self.summary.set(f"Установлено: {result.get('installed')} → архив: {result.get('candidate')}. Совместимость подтверждена.")
                                self.detail.set('Проверка завершена. Для переключения нажмите «Обновить службу».')
                    elif action == 'backup-verify':
                        if selected == self.backup_directory.get().strip():
                            try:
                                self.checked_backup.accept(selected, result)
                            except ValueError as error:
                                self.detail.set(str(error))
                            else:
                                kind_label = 'Портал' if result['kind'] == 'portal' else 'Организация'
                                self.backup_summary.set(f"{kind_label} • версия {result.get('version')} • файлов: {result.get('files')}. Копия проверена.")
                                self.detail.set('Можно восстановить копию отдельно от рабочей установки.')
                    elif action == 'restore-data':
                        self.checked_backup.clear()
                        self.recovery_output.set(result.get('directory', ''))
                        self.backup_summary.set(f"Восстановлено и проверено: таблиц {result.get('tables')}, вложений {result.get('attachments')}. База остановлена.")
                        self.detail.set('Восстановление проверено в отдельном каталоге. Рабочая установка не изменена; публикация не выполнялась.')
                    elif action == 'uninstall-check':
                        if result.get('status') == 'uninstall-ready' and result.get('service') == 'OwnDeskServer' and re.fullmatch(r'[a-f0-9]{64}', result.get('check_sha256', '')):
                            self.removal_checked = result
                            self.removal_summary.set(f"Служба: OwnDeskServer • версия {result['version']} • состояние {result['state']}\nДанные: {result['data']}")
                            self.detail.set('Служба проверена. Удаление доступно после ввода её имени.')
                        else:
                            self.removal_checked = None
                            self.removal_summary.set('Проверка службы не подтверждена.')
                    elif action == 'uninstall-remove':
                        self.removal_checked = None
                        self.removal_confirmation.set('')
                        self.removal_summary.set(f"Регистрация службы удалена. Данные сохранены: {result['data']}\nЖурнал: {result['receipt']}")
                        self.detail.set('Служба удалена; программа, база, файлы и ключи сохранены.')
                    else:
                        self.checked.clear()
                        self.transaction.set('')
                        self.detail.set('Обновление завершено. Служба готова.' if action == 'apply' else 'Прежняя программа запущена с текущей рабочей базой.')
                        self.summary.set(f"Версия: {result.get('version')}. Операция: {result.get('transaction')}.")
                self.refresh()
        self.tick_id = self.window.after(200, self.tick)

    def close(self):
        if self.busy: self.detail.set('Дождитесь результата. Во время обновления окно закрывать нельзя.')
        else:
            self.window.after_cancel(self.tick_id)
            self.window.destroy()


def main(preview=False, update_tab=False, recovery_tab=False, uninstall_tab=False):
    if not preview: service.admin_required()
    window = tk.Tk()
    app = InstallerWindow(window, preview=preview, update_tab=update_tab)
    if recovery_tab: app.tabs.select(2)
    if uninstall_tab: app.tabs.select(3)
    window.mainloop()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--preview', action='store_true', help='Show the layout without service operations')
    parser.add_argument('--preview-update', action='store_true', help='Preview the update tab without service operations')
    parser.add_argument('--preview-recovery', action='store_true', help='Preview backup recovery without service operations')
    parser.add_argument('--preview-uninstall', action='store_true', help='Preview service removal without service operations')
    args = parser.parse_args()
    main(preview=args.preview or args.preview_update or args.preview_recovery or args.preview_uninstall, update_tab=args.preview_update, recovery_tab=args.preview_recovery, uninstall_tab=args.preview_uninstall)
