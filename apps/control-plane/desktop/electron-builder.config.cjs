const isMacRelease = process.env.ONESTEP_MAC_RELEASE === "1";

/** @type {import("electron-builder").Configuration} */
module.exports = {
  appId: "com.onestep.controlplane",
  productName: "OneStep",
  directories: {
    output: "release",
  },
  files: ["out/**/*", "package.json"],
  extraResources: [
    { from: "./backend-dist/onestep-control-plane-api", to: "backend/onestep-control-plane-api" },
    { from: "../alembic.ini", to: "alembic.ini" },
    { from: "../backend/alembic", to: "backend/alembic" },
  ],
  mac: {
    category: "public.app-category.developer-tools",
    hardenedRuntime: isMacRelease,
    identity: isMacRelease ? undefined : null,
    notarize: isMacRelease,
    target: [
      { target: "dmg", arch: ["arm64", "x64"] },
      { target: "zip", arch: ["arm64", "x64"] },
    ],
  },
  dmg: {
    artifactName: "onestep-macos-${arch}.${ext}",
  },
};
