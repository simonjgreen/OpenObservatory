/// <reference types="vite/client" />

// Declares Vite's ambient types, including the side-effect CSS import in
// `main.tsx`. TypeScript 5.9 does not require this; TypeScript 7 does, and
// without it `npm run build` fails with
//
//   src/main.tsx(4,8): error TS2882: Cannot find module or type declarations
//   for side-effect import of './styles.css'
//
// Added ahead of that upgrade rather than inside it, so the version bump stays
// a version bump and this stays a source change that can be reviewed on its own.
