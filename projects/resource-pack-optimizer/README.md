# Resource Pack Optimizer

Java utility extracted from work on the Elseborn resource pipeline. It targets format 75, deduplicates compatible aliases and model references, and checks the active overlays when rewriting a ZIP. Project-specific prefixes reflect the original integration.

Requires JDK 17+ and Gson 2.11.0. Test with a copy of your pack because the tool rewrites its input.

```sh
javac -cp gson-2.11.0.jar -d out src/elseborn/packfix/AtlasCanonicalizer.java
java -cp 'out:gson-2.11.0.jar' elseborn.packfix.AtlasCanonicalizer copy.zip
```

Use a semicolon in place of the colon in the classpath on Windows. No game assets or plugin binaries are included.
