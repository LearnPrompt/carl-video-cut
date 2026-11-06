// 繁体转简体：whisper 中文模型偶尔整段输出繁体，转写和字幕都要简体。用 macOS 自带的 ICU 变换 Hant-Hans，逐字对应，不改长度。
// 用法：swift hant2hans.swift < in.txt > out.txt
import Foundation
let input = String(data: FileHandle.standardInput.readDataToEndOfFile(), encoding: .utf8) ?? ""
print(input.applyingTransform(StringTransform("Hant-Hans"), reverse: false) ?? input, terminator: "")
