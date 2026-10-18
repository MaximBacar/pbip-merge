// Runs test/suite.js inside a downloaded VS Code:  node test/runTest.js <workspace>
const path = require("path");
const { runTests } = require("@vscode/test-electron");

runTests({
  extensionDevelopmentPath: path.resolve(__dirname, ".."),
  extensionTestsPath: path.resolve(__dirname, "suite.js"),
  launchArgs: [process.argv[2], "--disable-extensions", "--skip-welcome", "--skip-release-notes"],
}).catch((err) => {
  console.error(err);
  process.exit(1);
});
