// Run only on the disposable CI runner. These tests never start the installer UI or a service.
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Text;
using System.Web.Script.Serialization;

internal static class InstallerValidationTests
{
    static readonly List<string> Passed = new List<string>();
    static readonly string[] Required = { "VERSION", "infra/windows_bootstrap.ps1", "infra/windows_python.ps1",
        "infra/windows-python.json", "infra/windows-dependencies.json", "infra/windows_server_window.py", "infra/INSTALLATION-NOTICE.txt" };

    static byte[] Archive(Action<ZipArchive> extra, string omit, string version)
    {
        using (MemoryStream bytes = new MemoryStream())
        {
            using (ZipArchive archive = new ZipArchive(bytes, ZipArchiveMode.Create, true))
            {
                foreach (string name in Required)
                    if (name != omit) Add(archive, "OwnDesk/" + name, name == "VERSION" ? version : "fixture");
                if (extra != null) extra(archive);
            }
            return bytes.ToArray();
        }
    }

    static void Add(ZipArchive archive, string name, string text)
    {
        using (StreamWriter writer = new StreamWriter(archive.CreateEntry(name).Open(), new UTF8Encoding(false))) writer.Write(text);
    }

    static void Reject(string label, Action check)
    {
        try { check(); }
        catch (InvalidDataException) { Passed.Add(label); return; }
        throw new InvalidOperationException("Unsafe input was accepted: " + label);
    }

    static int Main(string[] args)
    {
        try
        {
            if (args.Length != 2) throw new ArgumentException("Expected mode and a new report path");
            if (args[0] == "bad-resource")
                Reject("embedded payload SHA-256 mismatch", delegate { Setup.Payload(); });
            else if (args[0] == "validation")
            {
                if (Setup.Validate(Setup.Payload()) < Required.Length) throw new InvalidOperationException("Real payload rejected");
                Passed.Add("real embedded payload hash and structure");
                byte[] valid = Archive(null, null, Setup.Release);
                if (Setup.Validate(valid) != Required.Length) throw new InvalidOperationException("Fixture rejected");
                Passed.Add("minimal valid archive");
                foreach (string path in new[] { "../escape", "OwnDesk/../escape", "OwnDesk/./file", "OwnDesk//file",
                    "/OwnDesk/file", "Other/file", "OwnDesk\\file", "OwnDesk/C:/file", "OwnDesk/file:stream",
                    "OwnDesk/file.", "OwnDesk/file ", "OwnDesk/CON", "OwnDesk/nul.txt", "OwnDesk/COM1.txt", "OwnDesk/LPT9" })
                {
                    string name = path;
                    Reject("unsafe path " + name, delegate { Setup.Validate(Archive(delegate(ZipArchive a) { Add(a, name, "fixture"); }, null, Setup.Release)); });
                }
                Reject("duplicate file", delegate { Setup.Validate(Archive(delegate(ZipArchive a) { Add(a, "OwnDesk/VERSION", Setup.Release); }, null, Setup.Release)); });
                Reject("case-insensitive duplicate", delegate { Setup.Validate(Archive(delegate(ZipArchive a) { Add(a, "OwnDesk/version", Setup.Release); }, null, Setup.Release)); });
                Reject("file used as parent directory", delegate { Setup.Validate(Archive(delegate(ZipArchive a) { Add(a, "OwnDesk/conflict", "fixture"); Add(a, "OwnDesk/conflict/child", "fixture"); }, null, Setup.Release)); });
                Reject("symbolic link entry", delegate { Setup.Validate(Archive(delegate(ZipArchive a) { ZipArchiveEntry entry = a.CreateEntry("OwnDesk/link"); entry.ExternalAttributes = unchecked((int)0xA1FF0000); }, null, Setup.Release)); });
                foreach (string missing in Required)
                {
                    string name = missing;
                    Reject("missing required " + name, delegate { Setup.Validate(Archive(null, name, Setup.Release)); });
                }
                Reject("wrong version", delegate { Setup.Validate(Archive(null, null, "999.0.0")); });
                Reject("invalid ZIP bytes", delegate { Setup.Validate(Encoding.UTF8.GetBytes("not an archive")); });
                Reject("entry count limit", delegate { Setup.Validate(Archive(delegate(ZipArchive a) {
                    for (int i = 0; i < 5001; i++) Add(a, "OwnDesk/extra-" + i, "");
                }, null, Setup.Release)); });
                Reject("expanded size limit", delegate { Setup.Validate(Archive(delegate(ZipArchive a) {
                    using (Stream stream = a.CreateEntry("OwnDesk/large").Open()) {
                        byte[] block = new byte[1024 * 1024];
                        for (int i = 0; i < 129; i++) stream.Write(block, 0, block.Length);
                    }
                }, null, Setup.Release)); });
                string temp = Path.Combine(Path.GetTempPath(), "OwnDeskValidation-" + Guid.NewGuid().ToString("N"));
                Directory.CreateDirectory(temp);
                string sentinel = Path.Combine(temp, "sentinel.txt");
                File.WriteAllText(sentinel, "must remain unchanged");
                try
                {
                    try { Setup.Extract(valid, temp, false); throw new InvalidOperationException("Existing destination accepted"); }
                    catch (IOException) { }
                    if (File.ReadAllText(sentinel) != "must remain unchanged") throw new InvalidOperationException("Existing destination modified");
                    Passed.Add("existing extraction destination and sentinel preserved");
                }
                finally { File.Delete(sentinel); Directory.Delete(temp); }
            }
            else throw new ArgumentException("Unknown test mode");
            using (StreamWriter report = new StreamWriter(new FileStream(args[1], FileMode.CreateNew, FileAccess.Write), new UTF8Encoding(false)))
                report.Write(new JavaScriptSerializer().Serialize(new { success = true, passed = Passed.Count, checks = Passed, services_modified = false }));
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error.ToString()); return 1; }
    }
}
