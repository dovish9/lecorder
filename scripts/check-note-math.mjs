// Development-only parity runner; the application uses embedded V8.
import fs from 'node:fs';
import vm from 'node:vm';
import katex from '../web/static/vendor/katex/katex.mjs';
const context = vm.createContext({katex});
vm.runInContext(fs.readFileSync(new URL('../web/backend/check_math.js', import.meta.url), 'utf8'), context);
process.stdout.write(JSON.stringify(context.checkMath(JSON.parse(fs.readFileSync(0, 'utf8')))));
