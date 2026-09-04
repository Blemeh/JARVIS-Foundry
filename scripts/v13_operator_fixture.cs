using System;
using System.Drawing;
using System.IO;
using System.Text;
using System.Windows.Forms;

namespace JarvisV13OperatorFixture
{
    internal sealed class OperatorFixtureForm : Form
    {
        private readonly string documentPath;
        private readonly TextBox documentText;
        private readonly Label savedStatus;

        internal OperatorFixtureForm(string path, string title)
        {
            documentPath = Path.GetFullPath(path);

            Name = "OperatorFixtureWindow";
            AccessibleName = title;
            Text = title;
            AutoScaleMode = AutoScaleMode.None;
            ClientSize = new Size(620, 330);
            MinimumSize = new Size(620, 330);
            StartPosition = FormStartPosition.CenterScreen;

            var documentLabel = new Label();
            documentLabel.Name = "DocumentLabel";
            documentLabel.AccessibleName = "Document label";
            documentLabel.Text = "Document";
            documentLabel.Location = new Point(18, 18);
            documentLabel.Size = new Size(120, 24);

            documentText = new TextBox();
            documentText.Name = "DocumentText";
            documentText.AccessibleName = "Document";
            documentText.Multiline = true;
            documentText.ScrollBars = ScrollBars.Vertical;
            documentText.Location = new Point(18, 48);
            documentText.Size = new Size(584, 190);
            documentText.Text = File.Exists(documentPath)
                ? File.ReadAllText(documentPath, Encoding.UTF8)
                : String.Empty;

            var saveButton = new Button();
            saveButton.Name = "SaveButton";
            saveButton.AccessibleName = "Save";
            saveButton.Text = "Save";
            saveButton.Location = new Point(18, 255);
            saveButton.Size = new Size(110, 36);
            saveButton.Click += SaveDocument;

            var dialogButton = new Button();
            dialogButton.Name = "UnexpectedDialogButton";
            dialogButton.AccessibleName = "Open unexpected dialog";
            dialogButton.Text = "Open dialog";
            dialogButton.Location = new Point(146, 255);
            dialogButton.Size = new Size(138, 36);
            dialogButton.Click += OpenUnexpectedDialog;

            var crashButton = new Button();
            crashButton.Name = "CrashButton";
            crashButton.AccessibleName = "Crash fixture";
            crashButton.Text = "Crash";
            crashButton.Location = new Point(302, 255);
            crashButton.Size = new Size(110, 36);
            crashButton.Click += CrashFixture;

            savedStatus = new Label();
            savedStatus.Name = "SavedStatus";
            savedStatus.AccessibleName = "Saved";
            savedStatus.Text = "Saved";
            savedStatus.Location = new Point(438, 262);
            savedStatus.Size = new Size(150, 24);
            savedStatus.Visible = false;

            Controls.Add(documentLabel);
            Controls.Add(documentText);
            Controls.Add(saveButton);
            Controls.Add(dialogButton);
            Controls.Add(crashButton);
            Controls.Add(savedStatus);
        }

        private void SaveDocument(object sender, EventArgs eventArgs)
        {
            File.WriteAllText(
                documentPath,
                documentText.Text,
                new UTF8Encoding(false)
            );
            var countPath = documentPath + ".save-count";
            var count = 0;
            if (File.Exists(countPath))
            {
                Int32.TryParse(
                    File.ReadAllText(countPath, Encoding.ASCII),
                    out count
                );
            }
            File.WriteAllText(
                countPath,
                (count + 1).ToString(),
                Encoding.ASCII
            );
            savedStatus.Visible = false;
            var timer = new Timer();
            timer.Interval = 750;
            timer.Tick += delegate
            {
                timer.Stop();
                savedStatus.Visible = true;
                timer.Dispose();
            };
            timer.Start();
        }

        private void OpenUnexpectedDialog(object sender, EventArgs eventArgs)
        {
            var timer = new Timer();
            timer.Interval = 150;
            timer.Tick += delegate
            {
                timer.Stop();
                timer.Dispose();
                MessageBox.Show(
                    this,
                    "Disposable unexpected dialog",
                    "JARVIS V13 Unexpected Dialog",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information
                );
            };
            timer.Start();
        }

        private void CrashFixture(object sender, EventArgs eventArgs)
        {
            var timer = new Timer();
            timer.Interval = 100;
            timer.Tick += delegate
            {
                timer.Stop();
                timer.Dispose();
                Environment.Exit(97);
            };
            timer.Start();
        }
    }

    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            if (args.Length < 1 || String.IsNullOrWhiteSpace(args[0]))
            {
                return 2;
            }
            var path = args[args.Length - 1];
            var title = args.Length > 1 && !String.IsNullOrWhiteSpace(args[0])
                ? args[0]
                : "JARVIS V13 Operator Fixture";
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new OperatorFixtureForm(path, title));
            return 0;
        }
    }
}
