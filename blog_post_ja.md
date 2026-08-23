# 一行も書いていないシンセサイザーが、FPGAの上で鳴っている

**TL;DR**

- CPU が中に入っておらず、走らせるプログラムもないシンセサイザーを作った。音は
  2万円ほどの書き換え可能なチップの中に組まれたハードワイヤードのロジックから出て
  くる。32音同時、操作は USB ケーブルでつないだ Web ページから。
- この手の回路は普通、配線1本ずつ、クロック1回ずつ手で記述していく。そうではなく、
  音の計算を Rust に似た言語のふつうの関数として書き、Google のコンパイラにその関数
  から回路を作らせた。
- 設計をチップが読み込める形にコンパイルするには数分の重い計算がかかるので、それを
  ラップトップでは一度も走らせていない。ソースをクラウドの VM に上げると、できあがった
  チップ用のイメージと性能レポートが返ってくる。1周およそ6分。
- 私はその何ひとつ書いていない。設計をやったのは AI のコーディングエージェントで、
  版ごとに実機で音を鳴らし、出てきた音を録音し、本来含まれているべき周波数と突き
  合わせて0〜100点を付けて確認した。そういうテストが130件超。耳で判断したものは
  1つもない。
- 真似する価値があるのは AI の周りの仕掛けのほうだ。すべての機能が機械の採点できる
  数字を出すこと、そしてビルドと確認の1周が6分で終わること。

XLS32 は、2万円ほどの [FPGA](https://ja.wikipedia.org/wiki/FPGA) ボードの上に回路
として存在する、32音ポリフォニック・4パートマルチティンバーのシンセサイザーである。
オシレータ、ボイスごとのレゾナントフィルタ、エンベロープ、コーラス、ディレイ、
リバーブを備えていて、USB ケーブル1本でつないだブラウザ上のパネルから演奏する。全体を
[Apache-2.0 で公開](https://github.com/kazunori279/xls32-fpga-synth)しているので、
読む前に音を聴きたければ[デモ動画](https://youtu.be/2ROr9M_ZlVY)をどうぞ。

[![ブラウザのパネルが Basys 3 を駆動して Bach の Prelude in C を演奏している](docs/blog/assets/demo-video.jpg)](https://youtu.be/2ROr9M_ZlVY)

*左がブラウザのパネル、右が駆動されているボード。聞こえている音のサンプルはすべて
FPGA が計算している。*

このプロジェクトには変わっている点が3つあり、それらは互いに関係している。

設計は Verilog の手書きではなく、[Rust](https://www.rust-lang.org/) に似た
[DSLX](https://google.github.io/xls/dslx_reference/) という言語で書かれ、
[Google XLS](https://google.github.io/xls/) がハードウェアにコンパイルしている。
bitstream は1つも手元のラップトップでビルドしていない。すべて
[Google Compute Engine](https://cloud.google.com/compute) の VM から出てきたものだ。
そして、私はその何ひとつ書いていない。設計・実装・実機での検証まで、すべて
[Claude Code](https://claude.com/claude-code) (Opus 4.8) がネットワーク越しに、
誰もボードを見ていない状態でやった。機能のほとんどは、1週間の旅行中にスマートフォン
から開発されている。

同じ楽器を作るのは初めてではない。2012年に Altera DE0 の上で、8音のサイン波シンセを
[Verilog](https://ja.wikipedia.org/wiki/Verilog) で手書きした。1機能あたりだいたい
週末1回分かかり、そこで力尽きた。今回は、部屋に誰もいない状態でハードウェアが正しい
かどうかを確かめるループが回った。差がついたのはそこである。

この記事は FPGA に触ったことのないソフトウェアエンジニア向けなので、ハードウェアの
概念は出てきたところで説明する。

---

## この楽器が実際に何なのか

[減算合成](https://en.wikipedia.org/wiki/Subtractive_synthesis) (subtractive
synthesis) は、電子楽器の作り方として最も古く、最も一般的なものだ。倍音を豊富に含む
波形から始めて、そこから削っていく。

![Subtractive synth 101](docs/blog/assets/synth101.png)

この図のすべてのブロックが、XLS32 では回路として存在する。オシレータはカウンタを
saw、square、triangle、sine、noise に変換する。フィルタはスペクトルを削り、その
カットオフを動かすことがシンセらしい「音が動く」感じを作る。
[ADSR エンベロープ](https://en.wikipedia.org/wiki/Envelope_(music))
(attack, decay, sustain, release) は音量の時間変化を作り、撥弦系の音とパッドの違いは
ほぼこのカーブで決まる。エフェクトは広がりと空間を足す。

パッチ、つまり1つの音色は、これらのブロック全部の設定値でしかない。XLS32 では
パッチは28個の数字で、その1つ1つがブラウザのパネルから送られる
[MIDI](https://ja.wikipedia.org/wiki/MIDI) コントロールチェンジだ。回路はまったく
変わらない。違う28個の数字を渡せば、同じシリコンがベースにもパッドにもベルにもなる。

ボードは32音を4つの MIDI チャンネルで共有し、ボイスあたりオシレータ2基とサブ
オシレータ1基、4種類の応答を持つ
[state-variable フィルタ](https://en.wikipedia.org/wiki/State_variable_filter)を
ボイスごとに1基、ADSR をボイスごとに2基、
[LFO](https://en.wikipedia.org/wiki/Low-frequency_oscillation) をパートごとに1基、
そして [Freeverb](https://ccrma.stanford.edu/~jos/pasp/Freeverb.html) 系のリバーブを
含むステレオのエフェクトチェーンを持つ。音声は 32 kHz 16-bit ステレオ PCM として、
MIDI が入ってくるのと同じ USB ケーブルで戻ってくる。

## そもそもなぜ FPGA でシンセを作るのか

オシレータの作り方には妥当な選択肢が5つあり、それぞれ作りやすさとタイミング保証を
トレードしている。

![How synths are built](docs/blog/assets/build-options.png)

[Web Audio](https://developer.mozilla.org/ja/docs/Web/API/Web_Audio_API) は数行の
JavaScript でどこでも動く。[Serum](https://xferrecords.com/products/serum-2) や
[Vital](https://vital.audio/) のようなソフトシンセなら、豊かな機能と速い反復が手に
入る。どちらも CPU を OS と共有するので、タイミングはベストエフォートだ。
[AudioWorklet](https://developer.mozilla.org/ja/docs/Web/API/AudioWorklet) は128
サンプル単位でレンダリングし、その下には通常 10〜40 ms の OS バッファがあり、負荷が
上がれば劣化する。ディスクリートのアナログ回路は音と手触りが手に入る代わりに、1
ボイスあたりに実際のお金がかかる。[ASIC](https://ja.wikipedia.org/wiki/ASIC) は
量産単価が最も安く、NRE に数億円かかり、テープアウト後のやり直しはきかない。

FPGA はその中間にある。ASIC のコスト構造なしにハードウェアの決定性が手に入る、
というのがその位置づけだ。固定長のパイプラインが毎サンプルを同じスケジュールで
組み立てるので、データパスの遅延は数マイクロ秒に固定され、ジッタがない。1サンプル
あたりの仕事量が固定サイクル予算になっているため、どんなパッチを読み込んでも32音
すべてがサンプル周期の中で終わる。ADAS のカメラ、ライブミキシングコンソール、
レーダー、[HFT](https://ja.wikipedia.org/wiki/高頻度取引) に FPGA が入っているのも
同じ性質が理由で、そこでは締め切りを決めるのが物理だからだ。

代償は開発ループで、このプロジェクトが攻めているのはそこである。

## FPGA を1枚の図で

![FPGA 101](docs/blog/assets/fpga101.png)

FPGA はプログラマブルなロジックの格子だ。その大半は
[look-up table](https://ja.wikipedia.org/wiki/ルックアップテーブル) (LUT) と
[flip-flop](https://ja.wikipedia.org/wiki/フリップフロップ) で、LUT は入力の任意の
小さなブール関数を計算し、flip-flop は次のクロックエッジまで1ビットを覚え、
プログラマブルな配線ファブリックがそれらを記述どおりの回路につなぐ。縦のストライプ状
に並ぶ hardened な DSP slice がファブリックの外で高速な
[積和演算](https://en.wikipedia.org/wiki/Multiply%E2%80%93accumulate_operation)を
担当し、同じくストライプ状の block RAM がクロック同期読み出しのオンチップメモリを
提供する。

「プログラム」にあたるのが bitstream だ。どの LUT が何を計算し、どの配線がどこに
つながるかの記述で、電源投入時にロードされる。設計がそのままチップになる。

XLS32 のターゲットは [Digilent Basys
3](https://digilent.com/reference/programmable-logic/basys-3/start) に載った
[Xilinx
Artix-7](https://www.amd.com/ja/products/adaptive-socs-and-fpgas/fpga/artix-7.html)
`xc7a35t` で、20,800 LUT、90 DSP slice、36 Kb の block RAM が50個、100 MHz の
発振器が1つ。このクロックだと 32 kHz のサンプル間に 3,125 サイクルあり、設計全体は
この 3,125 サイクルをどう使うかという予算配分になる。

## Verilog と、その周りのビルドフロー

![Verilog and the standard dev flow](docs/blog/assets/verilog-flow.png)

Verilog はハードウェア記述言語である。
[レジスタ単位](https://en.wikipedia.org/wiki/Register-transfer_level)、配線単位、
クロック単位で回路を記述する。ソフトウェア出身者にとって本当に難しい性質が2つある。

すべてが同時に起きる。ファイル内のすべての `always @(posedge clk)` ブロックが、
すべてのクロックエッジで並列に発火する。上から下へ読める箇所はどこにもなく、
コールスタックも、かかるだけ時間のかかるループも、アロケーションもない。

スケジューリングは自分でやる。ある計算が1クロック周期で終わらないなら、自分で
ステージに分割し、どの中間値がどのサイクルにどのレジスタにいるかを手で決めなければ
ならない。間違えれば、設計は誤った答えを出すか、タイミングに落ちる。

さらにビルドがある。[論理合成](https://ja.wikipedia.org/wiki/論理合成)が RTL を
LUT・DSP・BRAM にマッピングし、
[place-and-route](https://en.wikipedia.org/wiki/Place_and_route) がそれらをダイ上の
どこに置きどう配線するかを決め、すべての信号が次のクロックエッジより前に到着するかを
検査する。place-and-route が遅い工程で、1回あたり数分から数時間かかり、しかも結果は
完全には決定的ではない。タイミングに落ちれば RTL の編集に逆戻りだ。

このループが、2012年のプロジェクトがサイン波8音で止まった理由である。

## Google XLS: ハードウェアをソフトウェアとして書く

![Google XLS 101](docs/blog/assets/xls101.png)

[高位合成](https://en.wikipedia.org/wiki/High-level_synthesis) (HLS) とは、
ソフトウェア的に書かれた振る舞いの記述を回路にコンパイルすることで、RTL はコンパイラ
が書く。[XLS](https://google.github.io/xls/) は Google のオープンソースな HLS
ツールキットだ。書くのは DSLX という、純粋関数と状態を持つ小さな `proc` からなる
Rust 風の言語で、スケジューリング、パイプラインレジスタの挿入、ビット幅の絞り込みは
コンパイラがやる。

以下は実際のコード（抜粋）だ。位相アキュムレータを波形に変換する、オシレータ1
サンプル分の処理:

```rust
// Bit-widths are types: u3/u32 unsigned, s16 signed wires.
fn voice_wave(wave: u3, phase: u32, noise: s16) -> s16 {
  let t = phase[24:32];          // cycle position 0..255
  match wave {
    u3:0 => SINE[t],                             // sine
    u3:1 => (t as s16) * s16:16 - s16:2048,      // saw
    u3:4 => noise,                               // noise
    _    => SINE[t],
  }
}
```

これは純粋関数である。合成できて、上から下に読めて、インタプリタ上でミリ秒単位で
unit test できる。ビット幅は型の一部で、`u3` は3ビットの符号なし配線、`s16` は
16ビットの符号付き配線、`phase[24:32]` はビットスライスで、ハードウェアでは無料だ。

これに対してコンパイラが吐くのは、だいたいこうなる:

```verilog
// pipeline registers p0/p1/p2 - inserted for you
always @(posedge clk) begin
  p0_t   <= phase[31:24];
  p1_sin <= SINE[p0_t];                  // 256-entry ROM
  p1_saw <= {p0_t, 4'h0} - 16'd2048;     // shift & offset
  p2_out <= (wave == 3'd0) ? p1_sin :
            (wave == 3'd1) ? p1_saw : p1_noise;   // wave-select mux
end
```

パイプラインステージが3段できている。3段にしてくれと頼んだ人はいない。コンパイラが
クロックに間に合わせるために必要な場所でデータフローグラフを切っただけで、目標周波数
や周囲のロジックを変えれば違う場所で切り直す。

トレードオフは実在するので、はっきり書いておく。サイクル精度の制御は手放すことに
なるので、それが必要な箇所（block RAM のポート、I/O プロトコル、クロックドメイン
クロッシング）では、生成されたコアの周りに手書きの Verilog シェルを置くことになる。
XLS32 ではこの分担がきれいに分かれていて、DSP の演算はすべて DSLX、メモリとピンは
シェルが持つ。

エージェントのループに効くのは図の最後の行である。unit test はインタプリタ上で
ミリ秒で走り、testbench もシミュレータもビルドも要らない。ほとんどの反復は FPGA の
ツールに一切触れない。

## 1つのファイルから bitstream まで

![DSLX to bitstream](docs/blog/assets/build-pipeline.png)

エンジン全体は `core/synth.x` の中の378行の `proc` 1つだ。そこから
`ir_converter` が XLS IR に落とし、`opt` が IR を最適化し、48ステージ指定の
`codegen --generator=pipeline` が `engine.v` を出す。小さな Python の後処理
スクリプトがグローバルなクロックイネーブルを注入し、オープンなツールチェーンが受け
付けない generate ループを展開する。あとは [yosys](https://yosyshq.net/yosys/) +
[VPR](https://verilogtorouting.org/) ([F4PGA](https://f4pga.org/))、
[nextpnr](https://github.com/YosysHQ/nextpnr)
([openXC7](https://github.com/openXC7))、または
[Vivado](https://www.amd.com/ja/products/software/adaptive-socs-and-fpgas/vivado.html)
が bitstream にし、
[openFPGALoader](https://github.com/trabucayre/openFPGALoader) が
[JTAG](https://ja.wikipedia.org/wiki/JTAG) 経由で書き込む。

これ全部を1コマンドで走らせ、bitstream とタイミングレポートの両方を持ち帰る:

```
STAGES=48 WCT=48 scripts/remote_build.sh
```

タイミングレポートはおまけの出力ではない。紙の上でタイミングを満たしたビルドと、
実際に測ったビルドは別物であり、エージェントにはこの数字を読むことを義務づけている。

---

## Loop engineering

このプロジェクトを表すプロンプトは「シンセを書いて」ではない。それより
「edit → build → run → observe の、自己検証できる短いサイクルを設計し、その中で
エージェントに反復させる」に近い。

![The loop](docs/blog/assets/the-loop.png)

エージェントが `synth.x` を編集する。ビルドが bitstream とタイミングレポートを出す。
JTAG でボードに書き込み、MIDI を流しながら同じ USB ケーブルで音声をキャプチャして
戻す。キャプチャは0〜100点で採点される。合格ならマイルストーン完了、リグレッション
なら編集に戻る。LED を見る人も、スピーカーを聴く人も、ボタンを押す人もいない。

### 聴かずにボードを採点する

![Autonomous verification](docs/blog/assets/verification.png)

エージェントは、測れるものにしか反復をかけられない。だから XLS32 のすべての機能は、
人間の感覚なしに機械が採点できる信号を出す必要があった。

音声は生のサンプル列としてボードから USB に分岐して出ている。音程は
[FFT](https://ja.wikipedia.org/wiki/高速フーリエ変換) で検証する。4音の和音は正しい
周波数に4本のピークが同時に立つことであり、これは数値的なアサーションだ。音色と
安定性は[スペクトログラム](https://ja.wikipedia.org/wiki/スペクトログラム)で検証
する。かすみ、クリップ、ドロップアウトは構造として見える。現在は130を超える採点
付きの実機 e2e テストがあり、基本機能・機能の組み合わせ・ストレスをカバーし、それぞれ
が0〜100点を出してリグレッション時に落ちる。

あるとき、音が明らかに壊れているのに FFT のピーク検査が通ってしまい、これで実際に
時間を失った。たまたま切り出したスライスがきれいで、
キャプチャの残りがそうでなかったのである。きれいな窓を1つ信じるのではなく、キャプチャ
全体をスペクトログラムにして、それを採点したほうがいい。

---

## ハードウェアをクラウド VM でビルドする

![Cycle time](docs/blog/assets/cycle-time.png)

FPGA 開発は、ツールチェーンをワークステーションに入れるものだと暗黙に前提している。
ベンダツールは数十 GB、ノードロックライセンス、x86 の Linux 専用で、みんなローカルに
入れてローカルでビルドする。ループを遅くしているのはこの前提で、そして捨ててみたら
簡単に捨てられた。

Apple Silicon の Mac では状況はさらに悪くなる。F4PGA は
[Docker](https://www.docker.com/) の x86 エミュレーション上で動かす必要があって1
ビルド10分ほどかかり、Vivado はそもそも動かない。そこでビルドを
[Compute Engine](https://cloud.google.com/compute) の VM に移し、ラップトップには
本当にローカルでなければならないもの、つまり USB ケーブルの先のボードだけを残した。

全体は [`gcloud`](https://cloud.google.com/sdk/gcloud) と `scp` と `ssh` だけの37行の
スクリプトで、CI システムもコンテナレジストリもアーティファクトストアもない:

```bash
gcloud compute scp --zone="$Z" --project="$P" \
  core/synth.x core/codegen.sh core/fix_verilog.py \
  boards/basys3/rtl/top.v boards/basys3/rtl/basys3.xdc \
  ... "$VM":~/build/
gcloud compute ssh "$VM" --zone="$Z" --project="$P" \
  --command="STAGES=${STAGES:-48} WCT=${WCT:-48} bash ~/build/$RB"
gcloud compute scp ... "$VM":~/build/top.bit ./build/top.bit
gcloud compute scp ... "$VM":~/build/timing.txt ./build/timing.txt
```

上がっていくのはソース7ファイル。戻ってくるのは bitstream、タイミングレポート、
そして Vivado の使用率・タイミングレポートだ。その間にあるもの（XLS、yosys、VPR、
nextpnr、Vivado、Xilinx のデバイスデータベース）はすべて VM 側にあり、ラップトップに
インストールされることは一度もない。

ここから3つのことが出てきた。素の高速化はその中で一番おもしろくない。

**ビルドが純粋関数になる。** ソースを入れると bitstream と実測のタイミング値が出て
くる、それがどこか別の場所で計算される。これはエージェントが駆動できる形そのものだ。
コマンド1つ、返ってくるファイル2つ、壊れるローカル状態なし、維持すべきインストール
なし。エージェントは Xilinx のデバイスデータベースが何なのかを知る必要が一度も
なかった。

**place-and-route バックエンドの切り替えが環境変数になる。** 3つのフローが同じ VM に
同居していて、`BACKEND=f4pga|nextpnr|vivado` が向こう側の3本のビルドスクリプトを
選ぶ。3つの place-and-route ツールの比較が、Python のバージョンで喧嘩する3つの
ローカルインストールではなく、変数1つになる。この記事の後半に出てくるバックエンド
比較は、そうやって測ったものだ。

**実時間が10分ほどから6分ほどに落ちる。** 4分の短縮はたいした差に見えないが、午後
じゅう反復したがるエージェントに掛け算すると効いてくる。1時間あたりの検証済み反復
回数がおおよそ2倍になり、ビルド中もラップトップが空いたままになる。

これが効くのは、この用途で速いツールチェーンが x86 の Linux 専用だからである。
ツールチェーンがオープンでネイティブに動くなら、往復させる
価値はない。同じエンジンは
[Lattice ECP5](https://www.latticesemi.com/Products/FPGAandCPLD/ECP5) のボードも
ターゲットにしていて、そちらは yosys + nextpnr の完全にオープンなフローが Apple
Silicon でネイティブに動き、コアを1分足らずでビルドするので、VM は一度も要らなかった。

もっと大きいのは、図で VM の上にある行のほうだ。DSLX の unit test はミリ秒で走り、
エンジンの [NumPy](https://numpy.org/) モデルは秒で走るので、ほとんどの反復はビルドを
1回も使わない。アイデアはソフトウェアモデルで先に証明し、6分のビルドは確認にだけ
使う。FM の強さ、リバーブのダンピング、プリセット探索は、どれもまずシミュレーション
で決着している。

---

## サインオフできる単位で積む

![M1 to M19](docs/blog/assets/milestones.png)

プロジェクトは19のマイルストーンとして進み、それぞれが単体で採点できる機能だった。
M1 は 8-bit・4 kHz の
[DDS](https://en.wikipedia.org/wiki/Direct_digital_synthesis) サイン波1つと線形
ADSR だけ。音は貧弱だったが、それは論点ではない。edit → build → flash → measure の
ループが閉じたことを証明したのが成果である。M3 で本物の MIDI 入力、
[UART](https://ja.wikipedia.org/wiki/UART) の受信とパーサ、ボイスアロケーションが
入り、FFT が音程を確認した。M6a は時分割多重の32音パイプラインエンジンへの書き直し。
M6b でボイスごとにレゾナントフィルタが付いた。M13 と M14 でコーラス、ピンポン
ディレイ、Freeverb が block RAM に入った。M19 でクロスオシレータの ring と FM。

このどれの裏にも、エージェントが超えなければならない数字がある。

## 浮動小数点シミュレータには出ない、固定小数点ハードウェアのバグ

実機でしか出なかったバグを2つ挙げる。どちらも
[固定小数点演算](https://ja.wikipedia.org/wiki/固定小数点数)から来ている。

フィルタがラッチした。レゾナンスが高いと、state-variable フィルタの積分器の状態
がクランプのレールに貼り付いてボイスが無音になる。明るいポリフォニックの FM では
フルスケールのリミットサイクルに入り、再生時間の96%でレールに張り付いていた。直し方は
積分器を漏らすこと（1サンプルあたり約1%）で、これでフィルタの極が単位円のわずかに
内側に入り、自励振動が減衰する。浮動小数点のシミュレーションでは一度も出なかった。

リバーブが暴走した。ダンピング係数のシフト量が小さいと可聴帯域を潰し、しかも
16ビットの状態がラップし得る。フィードバックループの中で符号が反転すれば暴走だ。
直し方はダンピングを `(old + new) / 2` で計算すること。これはオーバーフローし得ない。
加えてリセット時に block RAM をクリアし、電源投入時のゴミがループの種にならないように
する。

この2つの下にある一般則は、ラップさせるなクランプしろ、である。ラップは巨大な不連続
で、それは広帯域のクリック音になり、スペクトログラムには即座に出て、ピーク検査には
出ない。

---

## アーキテクチャ、手短に

設計原則は「1つのクロック、1つのサンプルレート」だ。すべてが純粋関数か小さな proc
のどちらかで、シンセはサンプルレートの1ティックにつき音声サンプルをちょうど1つ出す。

![One clock, three cadences](docs/blog/assets/clock-cadences.png)

ボード上の発振器は 100 MHz が1つだけで PLL はないので、遅いレートは本物の遅いクロック
ではなくクロックイネーブルで作る。エンジンは3分周のイネーブル 33.3 MHz で進み、
エフェクトのステートマシンは 16.7 MHz で1ステップ進む。32 kHz のサンプル周期は
3,125 マスタクロックで、32音のスキャンがそのうち約 2,304（74%）を使い、エフェクトの
パスと UART のフレームはそれと並行して走るので、周期の4分の1ほどが空く。

![Inside the 48 stages](docs/blog/assets/48-stages.png)

この48段のパイプラインは設計したものではない。XLS がクロックに間に合わせるために
データフローグラフを48枚に切った結果で、図の各ボックスは中のロジック量に比例した
大きさで描いてある。新しいボイスがパイプラインに入るのは24サイクルに1回なので、
同時に飛んでいるボイスは常に2つだけで、残りの段はバブルを運んでいる。それで問題は
ない。サンプルレートには余裕をもって間に合っていて、深さはタイミングの余裕を買って
いる。

![Where it lands on the chip](docs/blog/assets/chip-usage.png)

出荷している Vivado ビルドで制約になっているリソースは block RAM で 65%。ディレイ
ライン用の 16K×16 のリングバッファ4本が、50個ある BRAM のうち32個を食う。LUT は
50%、レジスタは 42%、そして90個ある DSP slice のうち26個が設計内のすべての乗算を
担当している。

![Three P&R backends](docs/blog/assets/backends.png)

同じ RTL を3つの place-and-route バックエンドがビルドしているが、出てくるチップは
同じではない。Vivado は DSP slice と block RAM を推論し、30 ns の予算に対して
クリティカルパス約 18.5 ns を出し、32 kHz で出荷している。openXC7 は完全にオープンで
本物の Fmax レポートを出すが、nextpnr が DSP の carry-cascade ピンをまだ配線できず、
乗算がファブリックに落ちるので4分周・28 kHz で動く。F4PGA は DSP も BRAM も推論
しないので、すべての乗算が LUT とキャリーによるソフト乗算器になり、チップの使用率は
90% ほどになる。

同一のソース、同一の生成 Verilog で、クリティカルパスに約2倍の差が出る。ボトル
ネックは XLS ではなかった。バックエンドのほうである。そしてその制約が設計を形作った。
乗算のオペランド型を狭く保って narrowing pass が縮められるようにする、同期読み出しが
要るものは手書きのシェルに置く、遅いクロックが作れないのでエンジン全体をグローバルな
クロックイネーブルで進める、といった具合だ。

---

## ソフトウェアエンジニアに伝えるとしたら

HLS は、DSP ハードウェアをエージェントが作業できる程度にはソフトウェアに近づける。
ただしサイクル精度の制御が要る箇所に、薄い手書きのシェルを許容することが条件になる。

エージェントは本物のハードウェアを作れるが、それは機械が採点できる信号を出す機能に
限られる。このシンセが成立したのは、音声を FFT とスペクトログラムで採点できるからだ。
耳でしか評価できない機能は、このループでは生き残らなかったはずである。

ツールチェーンを自分のマシンから追い出すこと。クラウド VM で走って bitstream と実測の
タイミング値を返すハードウェアビルドは、他のあらゆるリモートビルドと同じインタフェース
である。壊れるローカルインストールがなく、バックエンドは環境変数で差し替えられ、
1時間あたりの検証済み反復がおおよそ2倍になる。同じ
設計のミリ秒スケールのソフトウェアモデルと組み合わせれば、ほとんどの反復はコストが
ゼロになる。

そして、とにかく測ること。タイミングレポート、キャプチャ全体のスペクトログラム、
シミュレーションと実機の定期的なキャリブレーション。私が実際に時間を失ったバグは、
どれも「通ったように見えるもの」の裏に隠れていた。

コード、ビルド済み bitstream、ブロックごとのアーキテクチャ解説、マイルストーンごとの
開発ログは、すべて
[github.com/kazunori279/xls32-fpga-synth](https://github.com/kazunori279/xls32-fpga-synth)
にある。

*XLS32 は個人のサイドプロジェクトで、Apache-2.0 で公開している。ここに書いた意見は
私個人のものであり、所属組織のものではない。*
