using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: AssemblyTitle("OwnDesk Setup")]
[assembly: AssemblyProduct("OwnDesk")]
[assembly: AssemblyDescription("OwnDesk server installation wizard")]
[assembly: AssemblyVersion("@@VERSION@@.0")]

internal static class Setup
{
    internal const string Release = "@@VERSION@@";
    internal const string ExpectedHash = "@@SHA256@@";
    const int MaxEntries = 5000;
    const long MaxExpanded = 128L * 1024 * 1024;

    internal static byte[] Payload()
    {
        using (Stream source = Assembly.GetExecutingAssembly().GetManifestResourceStream("OwnDesk.Payload"))
        using (MemoryStream buffer = new MemoryStream())
        {
            if (source == null || source.Length > 32L * 1024 * 1024) throw new InvalidDataException("Нет допустимого дистрибутива OwnDesk.");
            source.CopyTo(buffer);
            byte[] bytes = buffer.ToArray();
            using (SHA256 hash = SHA256.Create())
                if (BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant() != ExpectedHash)
                    throw new InvalidDataException("Дистрибутив повреждён. Скачайте установщик заново с owndesk.work.");
            return bytes;
        }
    }

    static string RelativeName(ZipArchiveEntry entry)
    {
        string name = entry.FullName;
        if (!name.StartsWith("OwnDesk/", StringComparison.Ordinal) || name.IndexOf('\\') >= 0 ||
            ((entry.ExternalAttributes >> 16) & 0xF000) == 0xA000)
            throw new InvalidDataException("Недопустимый путь в дистрибутиве.");
        foreach (string part in name.Split('/'))
            if (part.Length == 0 || part == "." || part == ".." || part.EndsWith(".") || part.EndsWith(" ") ||
                part.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 ||
                Regex.IsMatch(part, @"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$", RegexOptions.IgnoreCase))
                throw new InvalidDataException("Недопустимое имя файла в дистрибутиве.");
        return name.Replace('/', Path.DirectorySeparatorChar);
    }

    internal static int Validate(byte[] bytes)
    {
        using (ZipArchive archive = new ZipArchive(new MemoryStream(bytes), ZipArchiveMode.Read))
        {
            if (archive.Entries.Count > MaxEntries) throw new InvalidDataException("Слишком много файлов.");
            HashSet<string> names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            long total = 0;
            foreach (ZipArchiveEntry entry in archive.Entries)
            {
                string name = RelativeName(entry);
                if (!names.Add(name)) throw new InvalidDataException("Повторяющиеся файлы в дистрибутиве.");
                total = checked(total + entry.Length);
                if (total > MaxExpanded) throw new InvalidDataException("Слишком большой дистрибутив.");
            }
            foreach (string name in names)
                for (string parent = Path.GetDirectoryName(name); !String.IsNullOrEmpty(parent); parent = Path.GetDirectoryName(parent))
                    if (names.Contains(parent)) throw new InvalidDataException("Конфликт файлов и папок.");
            foreach (string required in new[] { "VERSION", "infra/windows_bootstrap.ps1", "infra/windows_python.ps1",
                "infra/windows-python.json", "infra/windows-dependencies.json", "infra/windows_server_window.py" })
                if (archive.GetEntry("OwnDesk/" + required) == null) throw new InvalidDataException("Неполный дистрибутив OwnDesk.");
            using (StreamReader reader = new StreamReader(archive.GetEntry("OwnDesk/VERSION").Open()))
                if (reader.ReadToEnd().Trim() != Release) throw new InvalidDataException("Версия дистрибутива не совпадает.");
            return names.Count;
        }
    }

    static void NoLinks(string path)
    {
        for (DirectoryInfo item = new DirectoryInfo(Path.GetFullPath(path)); item != null; item = item.Parent)
            if (item.Exists && (item.Attributes & FileAttributes.ReparsePoint) != 0)
                throw new IOException("Каталог установщика не должен быть ссылкой или junction.");
    }

    internal static string Extract(byte[] bytes, string directory, bool protect)
    {
        Validate(bytes); NoLinks(directory);
        if (Directory.Exists(directory) || File.Exists(directory)) throw new IOException("Выберите новый каталог. Существующие файлы не перезаписываются.");
        if (protect)
        {
            DirectorySecurity acl = new DirectorySecurity();
            acl.SetAccessRuleProtection(true, false);
            foreach (WellKnownSidType type in new[] { WellKnownSidType.BuiltinAdministratorsSid, WellKnownSidType.LocalSystemSid })
                acl.AddAccessRule(new FileSystemAccessRule(new SecurityIdentifier(type, null), FileSystemRights.FullControl,
                    InheritanceFlags.ContainerInherit | InheritanceFlags.ObjectInherit, PropagationFlags.None, AccessControlType.Allow));
            Directory.CreateDirectory(directory, acl);
        }
        else Directory.CreateDirectory(directory);
        string root = Path.GetFullPath(directory) + Path.DirectorySeparatorChar;
        using (ZipArchive archive = new ZipArchive(new MemoryStream(bytes), ZipArchiveMode.Read))
            foreach (ZipArchiveEntry entry in archive.Entries)
            {
                string target = Path.GetFullPath(Path.Combine(directory, RelativeName(entry)));
                if (!target.StartsWith(root, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("Путь выходит за каталог установки.");
                Directory.CreateDirectory(Path.GetDirectoryName(target));
                NoLinks(Path.GetDirectoryName(target));
                using (Stream source = entry.Open())
                using (FileStream output = new FileStream(target, FileMode.CreateNew, FileAccess.Write, FileShare.None)) source.CopyTo(output);
            }
        return Path.Combine(directory, "OwnDesk");
    }

    static void Report(string path, object value)
    {
        using (StreamWriter output = new StreamWriter(new FileStream(path, FileMode.CreateNew, FileAccess.Write), new UTF8Encoding(false)))
            output.Write(new JavaScriptSerializer().Serialize(value));
    }

    [STAThread]
    static int Main(string[] args)
    {
        bool diagnostic = args.Length > 0;
        try
        {
            if (args.Length == 2 && args[0] == "--verify")
            {
                int count = Validate(Payload());
                Report(args[1], new { success = true, version = Release, payload_sha256 = ExpectedHash, files = count });
                return 0;
            }
            if (args.Length == 3 && args[0] == "--extract-test")
            {
                string root = Extract(Payload(), args[1], false);
                Report(args[2], new { success = true, version = Release, root = root, processes_started = 0, services_modified = false });
                return 0;
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            if (args.Length == 3 && args[0] == "--preview")
            {
                using (Wizard form = new Wizard(true, args[1])) Application.Run(form);
                Report(args[2], new { success = true, preview = true, services_modified = false });
                return 0;
            }
            if (args.Length != 0) throw new ArgumentException("Неизвестные параметры запуска.");
            using (WindowsIdentity identity = WindowsIdentity.GetCurrent())
                if (!new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator))
                {
                    ProcessStartInfo elevated = new ProcessStartInfo(Application.ExecutablePath);
                    elevated.UseShellExecute = true; elevated.Verb = "runas";
                    using (Process child = Process.Start(elevated)) { child.WaitForExit(); return child.ExitCode; }
                }
            using (Wizard form = new Wizard(false, null)) Application.Run(form);
            return Wizard.Result;
        }
        catch (Exception error)
        {
            if (diagnostic) { Console.Error.WriteLine(error.Message); return 2; }
            string message = error is Win32Exception && ((Win32Exception)error).NativeErrorCode == 1223
                ? "Запуск отменён в окне Windows. Для установки службы необходимо подтверждение администратора."
                : error.Message;
            MessageBox.Show(message, "OwnDesk — не удалось запустить установщик", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }

    internal sealed class Wizard : Form
    {
        internal static int Result = 0;
        readonly bool preview;
        readonly string image;
        readonly Label status;
        public Wizard(bool previewMode, string imagePath)
        {
            preview = previewMode; image = imagePath;
            Text = "OwnDesk — установка сервера"; ClientSize = new Size(650, 250);
            StartPosition = FormStartPosition.CenterScreen; FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false; BackColor = Color.FromArgb(11,18,32); ForeColor = Color.FromArgb(240,245,255);
            Font = new Font("Segoe UI", 10);
            Controls.Add(new Label { Text = "OwnDesk", Font = new Font("Segoe UI", 24, FontStyle.Bold), AutoSize = true, Location = new Point(25,20) });
            status = new Label { Text = "Проверяем установщик и готовим мастер…", Location = new Point(29,85), Size = new Size(590,60) };
            Controls.Add(status);
            Controls.Add(new ProgressBar { Style = ProgressBarStyle.Marquee, Location = new Point(29,153), Size = new Size(590,20) });
            Controls.Add(new Label { Text = "Версия " + Release + " · Программа установки OwnDesk Server", Location = new Point(29,196), AutoSize = true, ForeColor = Color.FromArgb(168,185,210) });
            ControlBox = preview;
            Shown += Start;
        }
        void Start(object sender, EventArgs args)
        {
            if (preview)
            {
                using (Bitmap bitmap = new Bitmap(Width, Height)) { DrawToBitmap(bitmap, new Rectangle(Point.Empty, Size)); bitmap.Save(image); }
                BeginInvoke(new Action(Close)); return;
            }
            BackgroundWorker worker = new BackgroundWorker();
            worker.DoWork += delegate {
                byte[] payload = Payload(); Setup.Validate(payload);
                string directory = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "OwnDeskSetup-" + Release + "-" + Guid.NewGuid().ToString("N"));
                string root = Extract(payload, directory, true);
                string shell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell", "v1.0", "powershell.exe");
                BeginInvoke(new Action(delegate { status.Text = "Открываем мастер установки OwnDesk…\nПервый запуск может занять несколько минут."; }));
                ProcessStartInfo process = new ProcessStartInfo(shell, "-NoProfile -ExecutionPolicy Bypass -File \"" + Path.Combine(root,"infra","windows_bootstrap.ps1") + "\"");
                process.UseShellExecute = false; process.CreateNoWindow = true; process.WorkingDirectory = root;
                using (Process child = Process.Start(process)) { child.WaitForExit(); if (child.ExitCode != 0)
                    throw new InvalidOperationException("Мастер не смог запуститься или завершился с ошибкой.\nЖурнал: " + Path.Combine(root,".runtime","bootstrap-error.log")); }
            };
            worker.RunWorkerCompleted += delegate(object source, RunWorkerCompletedEventArgs completed) {
                if (completed.Error != null) { Result = 1; MessageBox.Show(this, completed.Error.Message, "OwnDesk — ошибка установки", MessageBoxButtons.OK, MessageBoxIcon.Error); }
                Close(); worker.Dispose();
            };
            worker.RunWorkerAsync();
        }
    }
}
