import threading
from pathlib import Path
from typing import Final, Self, Sequence

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

#https://www.unicode.org/charts/PDF/U2B00.pdf
#google提供的：NotoSansSymbols2-Regular
#可以支持上面文档的unicode

# Wingdings 字体完整映射表(0xF020-0xF0FF)
#参考：https://www.alanwood.net/demos/wingdings.html
#注意：上面的文档使用的范围为0x20-0xff，转化为0xf020-0xff
WINGDINGS_MAP = {
    0xF020: ' ',   # space
    0xF021: '🖉',  # pencil          → U+1F589
    0xF022: '✂',  # scissors        → U+2702
    0xF023: '✁',  # scissorscutting → U+2701
    0xF024: '👓',  # readingglasses  → U+1F453 no dingbat
    0xF025: '🕭',  # bell            → U+1F56D no dingbat
    0xF026: '🕮',  # book            → U+1F56E no dingbat
    0xF027: '🕯',  # candle          → U+1F56F no dingbat
    0xF028: '🕿',  # telephonesolid  → U+1F57F
    0xF029: '✆',  # telhandsetcirc  → U+2706
    0xF02A: '🖂',  # envelopeback    → U+1F582
    0xF02B: '🖃',  # envelopefront   → U+1F583 Dingbats (同 envelopeback)
    0xF02C: '📪',  # mailboxflagdwn  → U+1F4EA no dingbat
    0xF02D: '📫',  # mailboxflagup   → U+1F4EB no dingbat
    0xF02E: '📬',  # mailbxopnflgup  → U+1F4EC no dingbat
    0xF02F: '📭',  # mailbxopnflgdwn → U+1F4ED no dingbat
    0xF030: '📁',  # folder          → U+1F4C1 no dingbat
    0xF031: '📂',  # folderopen      → U+1F4C2 no dingbat
    0xF032: '📄',  # filetalltext1   → U+1F4C4 no dingbat
    0xF033: '🗏',  # filetalltext    → U+1F5CF no dingbat
    0xF034: '🗐',  # filetalltext3   → U+1F5D0 no dingbat
    0xF035: '🗄',  # filecabinet     → U+1F5C4 no dingbat
    0xF036: '⌛',  # hourglass       → U+231B no dingbat
    0xF037: '🖮',  # keyboard        → U+1F5AE no dingbat
    0xF038: '🖰',  # mouse2button    → U+1F5B0 no dingbat
    0xF039: '🖲',  # ballpoint       → U+1F5B2
    0xF03A: '🖳',  # pc              → U+1F5B3 no dingbat
    0xF03B: '🖴',  # harddisk        → U+1F5B4 no dingbat
    0xF03C: '🖫',  # floppy3         → U+1F5AB no dingbat
    0xF03D: '🖬',  # floppy5         → U+1F5AC no dingbat
    0xF03E: '✇',  # tapereel        → U+2707 no dingbat
    0xF03F: '✍',  # handwrite       → U+270D
    0xF040: '🖎',  # handwriteleft   → U+1F58E
    0xF041: '✌',  # handv           → U+270C
    0xF042: '👌',  # handok          → U+1F44C no dingbat
    0xF043: '👍',  # thumbup         → U+1F44D no dingbat
    0xF044: '👎',  # thumbdown       → U+1F44E no dingbat
    0xF045: '☜',  # handptleft      → U+261C
    0xF046: '☞',  # handptright     → U+261E
    0xF047: '☝',  # handptup        → U+261D
    0xF048: '☟',  # handptdwn       → U+261F
    0xF049: '🖐',  # handhalt        → U+1F590 no dingbat
    0xF04A: '☺',  # smileface       → U+263A
    0xF04B: '😐',  # neutralface     → U+1F610 no dingbat
    0xF04C: '☹',  # frownface       → U+2639
    0xF04D: '💣',  # bomb            → U+1F4A3 no dingbat
    0xF04E: '☠',  # skullcrossbones → U+2620
    0xF04F: '🏳',  # flag            → U+1F3F3
    0xF050: '🏱',  # pennant         → U+1F3F1
    0xF051: '✈',  # airplane        → U+2708
    0xF052: '☼',  # sunshine        → U+263C
    0xF053: '💧',  # droplet         → U+1F4A7 
    0xF054: '❄',  # snowflake       → U+2744
    0xF055: '🕆',  # crossoutline    → U+1F546
    0xF056: '✞',  # crossshadow     → U+271E
    0xF057: '🕈',  # crossceltic     → U+1F548
    0xF058: '✠',  # crossmaltese    → U+2720
    0xF059: '✡',  # starofdavid     → U+2721
    0xF05A: '☪',  # crescentstar    → U+262A
    0xF05B: '☯',  # yinyang         → U+262F
    0xF05C: 'ॐ',  # om              → U+0950 no dingbat
    0xF05D: '☸',  # wheel           → U+2638
    0xF05E: '♈',  # aries           → U+2648
    0xF05F: '♉',  # taurus          → U+2649
    0xF060: '♊',  # gemini          → U+264A
    0xF061: '♋',  # cancer          → U+264B
    0xF062: '♌',  # leo             → U+264C
    0xF063: '♍',  # virgo           → U+264D
    0xF064: '♎',  # libra           → U+264E
    0xF065: '♏',  # scorpio         → U+264F
    0xF066: '♐',  # saggitarius     → U+2650
    0xF067: '♑',  # capricorn       → U+2651
    0xF068: '♒',  # aquarius        → U+2652
    0xF069: '♓',  # pisces          → U+2653
    0xF06A: '🙰',  # ampersanditlc                  → U+1F670
    0xF06B: '🙵',  # ampersandit                    → U+1F675
    0xF06C: '●',  # circle6         → U+25CF
    0xF06D: '🔾',  # circleshadowdwn → U+1F53E
    0xF06E: '■',  # square6         → U+25A0
    0xF06F: '□',  # box3            → U+25A1 Geometric Shapes
    0xF070: '🞐',  # box4            → U+1F790 Geometric Shapes
    0xF071: '❑',  # boxshadowdwn    → U+2751 Dingbats
    0xF072: '❒',  # boxshadowup     → U+2752 Dingbats
    0xF073: '⬧',  # lozenge4        → U+2B27 Misc Symbols and Arrows
    0xF074: '⧫',  # lozenge6        → U+29EB Misc Symbols
    0xF075: '◆',  # rhombus6        → U+25C6 Geometric Shapes
    0xF076: '❖',  # xrhombus        → U+2756 Dingbats
    0xF077: '⬥',  # rhombus4        → U+2B25 Geometric Shapes
    0xF078: '⌧',  # clear           → U+2327
    0xF079: '⮹',  # escape          → U+2BB9
    0xF07A: '⌘',  # command         → U+2318
    0xF07B: '🏵',  # rosette         → U+1F3F5
    0xF07C: '🏶',  # rosettesolid    → U+1F3F6
    0xF07D: '🙶',  # quotedbllftbld  → U+1F676
    0xF07E: '🙷',  # quotedblrtbld   → U+1F677
    0xF080: '⓪',  # zerosans                       → U+24EA
    #这些都有2套
    #①-⑩ 0x2460-0x2469
    #❶-❿ 0x2766-0x277f
    #➀-➉ 0x2780-0x2789
    #➊-➓ 0x278a-0x2793
    0xF081: '①',  # onesans                        → U+2460
    0xF082: '②',  # twosans                        → U+2461
    0xF083: '③',  # threesans                      → U+2462
    0xF084: '④',  # foursans                       → U+2463
    0xF085: '⑤',  # fivesans                       → U+2464
    0xF086: '⑥',  # sixsans                        → U+2465
    0xF087: '⑦',  # sevensans                      → U+2466
    0xF088: '⑧',  # eightsans                      → U+2467
    0xF089: '⑨',  # ninesans                       → U+2468
    0xF08A: '⑩',  # tensans                        → U+2469

    #
    0xF08B: '⓿',  # zerosansinv                    → U+24FF
    0xF08C: '❶',  # onesansinv     → U+2776
    0xF08D: '❷',  # towsansinv      → U+2777
    0xF08E: '❸',  # thresssansinv      → U+2778
    0xF08F: '❹',  # foursansinv    → U+2779
    0xF090: '❺',  # fivesansinv     → U+277A
    0xF091: '❻',  # sixsansinv     → U+277B
    0xF092: '❼',  # servensansinv      → U+277C
    0xF093: '❽',  # eightsansinv    → U+277D
    0xF094: '❾',  # ninesansinv    → U+277E
    0xF095: '❿',  # tensansinv     → U+277F
    0xF096: '🙢',  # budleafne       → U+1F662 no dingbat
    0xF097: '🙠',  # budleafnw       → U+1F660 no dingbat
    0xF098: '🙡',  # budleafsw       → U+1F661 no dingbat
    0xF099: '🙣',  # budleafse       → U+1F663 no dingbat
    0xF09A: '🙞',  # vineleafboldne  → U+1F65E no dingbat
    0xF09B: '🙜',  # vineleafboldnw  → U+1F65C no dingbat
    0xF09C: '🙝',  # vineleafboldsw  → U+1F65D no dingbat
    0xF09D: '🙟',  # vineleafboldse  → U+1F65F no dingbat
    0xF09E: '·',  # circle2         → U+00B7 Geometric Shapes
    0xF09F: '•',  # circle4         → U+2022 Geometric Shapes
    0xF0A0: '▪',  # square2         → U+25AA Misc Technical
    0xF0A1: '⚪',  # ring2           → U+26AA Geometric Shapes
    0xF0A2: '🞆',  # ring4           → U+1F786 Geometric Shapes
    0xF0A3: '🞈',  # ring6           → U+1F788 Geometric Shapes
    0xF0A4: '◉',  # ringbutton2     → U+25C9 Misc Mathematical Symbols-B
    0xF0A5: '◎',  # target          → U+25CE Misc Symbols and Pictographs
    0xF0A6: '🔿',  # circleshadowup  → U+1F53F Geometric Shapes
    0xF0A7: '▪',  # square4         → U+25AA
    0xF0A8: '◻',  # box2            → U+25FB
    0xF0A9: '🟂',  # tristar2        → U+1F7C2
    0xF0AA: '✦',  # crosstar2       → U+2726
    0xF0AB: '★',  # pentastar2      → U+2605
    0xF0AC: '✶',  # hexstar2        → U+2736
    0xF0AD: '✴',  # octastar2       → U+2734
    0xF0AE: '✹',  # dodecastar3     → U+2739
    0xF0AF: '✵',  # octastar4       → U+2735
    0xF0B0: '⯐',  # registersquare  → U+2BD0
    0xF0B1: '⌖',  # registercircle  → U+2316
    0xF0B2: '⟡',  # cuspopen        → U+27E1
    0xF0B3: '⌑',  # cuspopen1       → U+2311
    0xF0B4: '⯑',  # query                          → U+2BD1
    0xF0B5: '✪',  # circlestar      → U+272A
    0xF0B6: '✰',  # starshadow      → U+2730
    #
    0xF0B7: '🕐',  # oneoclock       → U+1F550 no dingbat
    0xF0B8: '🕑',  # twooclock       → U+1F551 no dingbat
    0xF0B9: '🕒',  # threeoclock     → U+1F552 no dingbat
    0xF0BA: '🕓',  # fouroclock      → U+1F553 no dingbat
    0xF0BB: '🕔',  # fiveoclock      → U+1F554 no dingbat
    0xF0BC: '🕕',  # sixoclock       → U+1F555 no dingbat
    0xF0BD: '🕖',  # sevenoclock     → U+1F556 no dingbat
    0xF0BE: '🕗',  # eightoclock     → U+1F557 no dingbat
    0xF0BF: '🕘',  # nineoclock      → U+1F558 no dingbat
    0xF0C0: '🕙',  # tenoclock       → U+1F559 no dingbat
    0xF0C1: '🕚',  # elevenoclock    → U+1F55A no dingbat
    0xF0C2: '🕛',  # twelveoclock    → U+1F55B no dingbat
    #标准的是2BB0-2BB7
    0xF0C3: '⮰',  # arrowdwnleft1   → U+2BB0 Arrows
    0xF0C4: '⮱',  # arrowdwnrt1     → U+2BB1 Arrows
    0xF0C5: '⮲',  # arrowupleft1    → U+2BB2 Arrows
    0xF0C6: '⮳',  # arrowuprt1      → U+2BB3 Arrows
    0xF0C7: '⮴',  # arrowleftup1    → U+2BB4 (closest)
    0xF0C8: '⮵',  # arrowrtup1      → U+2BB5
    0xF0C9: '⮶',  # arrowleftdwn1   → U+2BB6
    0xF0CA: '⮷',  # arrowrtdwn1     → U+2BB7
    #================
    0xF0CB: '🙪',  # quiltsquare2    → U+1F66A
    0xF0CC: '🙫',  # quiltsquare2inv → U+1F66B
    0xF0CD: '🙕',  # leafccwsw       → U+1F655 no dingbat
    0xF0CE: '🙔',  # leafccwnw       → U+1F654 no dingbat
    0xF0CF: '🙗',  # leafccwse       → U+1F657 no dingbat
    0xF0D0: '🙖',  # leafccwne       → U+1F656 no dingbat
    0xF0D1: '🙐',  # leafnw          → U+1F650 no dingbat
    0xF0D2: '🙑',  # leafsw          → U+1F651 no dingbat
    0xF0D3: '🙒',  # leafne          → U+1F652 no dingbat
    0xF0D4: '🙓',  # leafse          → U+1F653 no dingbat
    0xF0D5: '⌫',  # deleteleft      → U+232B
    0xF0D6: '⌦',  # deleteright     → U+2326
    #➣这个为\u27a3
    #标准的unicode官方定义了，但是还没有支持
    0xF0D7: '⮘',  # head2left       → U+2B98
    0xF0D8: '⮚',  # head2right      → U+2B9A
    0xF0D9: '⮙',  # head2up         → U+2B99
    0xF0DA: '⮛',  # head2down       → U+2B9B
    #==========
    0xF0DB: '⮈',  # circleleft      → U+2B88
    0xF0DC: '⮊',  # circleright     → U+2B8A
    0xF0DD: '⮉',  # circleup        → U+2B89
    0xF0DE: '⮋',  # circledown      → U+2B8B
    #标准的是从：2B60-2B69
    0xF0DF: '🡨',  # barb2left       → U+1F868
    0xF0E0: '🡪',  # barb2right      → U+1F86A
    0xF0E1: '🡩',  # barb2up         → U+1F869
    0xF0E2: '🡫',  # barb2down       → U+1F86B
    0xF0E3: '🡬',  # barb2nw         → U+1F86C
    0xF0E4: '🡭',  # barb2ne         → U+1F86D
    0xF0E5: '🡯',  # barb2sw         → U+1F86F
    0xF0E6: '🡮',  # barb2se         → U+1F86E
    #====2B05-2B0D
    0xF0E7: '🡸',  # barb4left       → U+1F878
    #这个没有一致的
    0xF0E8: '🡺',  # barb4right      → U+1F87A
    0xF0E9: '🡹',  # barb4up         → U+1F879
    0xF0EA: '🡻',  # barb4down       → U+1F87B
    0xF0EB: '🡼',  # barb4nw         → U+1F87C
    0xF0EC: '🡽',  # barb4ne         → U+1F87D
    0xF0ED: '🡿',  # barb4sw         → U+1F87F
    0xF0EE: '🡾',  # barb4se         → U+1F87E
    #===========2B00-2B04
    0xF0EF: '⇦',  # bleft           → U+21E6
    0xF0F0: '⇨',  # bright          → U+21E8
    0xF0F1: '⇧',  # bup             → U+21E7
    0xF0F2: '⇩',  # bdown           → U+21E9
    0xF0F3: '⬄',  # bleftright      → U+2B04
    #\u21f3,\u21d5
    0xF0F4: '⇳',  # bupdown         → U+21F3
    0xF0F5: '⬀',  # bnw             → U+2B00 
    0xF0F6: '⬁',  # bne             → U+2B01 
    0xF0F7: '⬃',  # bsw             → U+2B03 
    0xF0F8: '⬂',  # bse             → U+2B02 
    0xF0F9: '🢬',  # bdash1          → U+1F8AC
    0xF0FA: '🢭',  # bdash2          → U+1F8AD
    #====
    0xF0FB: '🗶',  # xmarkbld        → U+1F5F6
    0xF0FC: '✔',  # checkbld        → U+2714
    0xF0FD: '🗷',  # boxxmarkbld     → U+1F5F7
    0xF0FE: '🗹',  # boxcheckbld     → U+1F5F9
    0xF0FF: '⊞',  # windowslogo     → U+229E
}


# Wingdings2 字体完整映射表 (0xF020-0xF0F9)
#https://www.alanwood.net/demos/wingdings-2.html
#注意：上面的文档使用的范围为0x20-0xff，转化为0xf020-0xff
WINGDINGS2_MAP = {
    0xF020: ' ',   # space
    0xF021: '🖊',  # penballpoint    → U+1F58A Dingbats
    0xF022: '🖋',  # penfountain     → U+1F58B Dingbats
    0xF023: '🖌',  # brush           → U+1F58C
    0xF024: '🖍',  # crayon          → U+1F58D
    0xF025: '✄',  # scissorsoutline → U+2704 Dingbats
    0xF026: '✀',  # scissorschilds  → U+2700
    0xF027: '🕾',  # telephone       → U+1F57E Dingbats
    0xF028: '🕽',  # telhandset      → U+1F57D
    0xF029: '🗅',  # file1           → U+1F5C5
    0xF02A: '🗆',  # file            → U+1F5C6
    0xF02B: '🗇',  # file3           → U+1F5C7
    0xF02C: '🗈',  # filetext1       → U+1F5C8
    0xF02D: '🗉',  # filetext        → U+1F5C9
    0xF02E: '🗊',  # filetext3       → U+1F5CA
    0xF02F: '🗋',  # filetall1       → U+1F5CB
    0xF030: '🗌',  # filetall        → U+1F5CC
    0xF031: '🗍',  # filetall3       → U+1F5CD
    0xF032: '📋',  # clipboard       → U+1F4CB
    0xF033: '🗑',  # trashcan        → U+1F5D1
    0xF034: '🗔',  # window          → U+1F5D4
    0xF035: '🖵',  # monitor         → U+1F5B5
    0xF036: '🖶',  # printer         → U+1F5B6
    0xF037: '🖷',  # fax             → U+1F5B7
    0xF038: '🖸',  # cd              → U+1F5B8
    0xF039: '🖭',  # tapecartridge   → U+1F5AD
    0xF03A: '🖯',  # mouse1button    → U+1F5AF
    0xF03B: '🖱',  # mouse3button    → U+1F5B1
    0xF03C: '🖒',  # thumbbackup     → U+1F592
    0xF03D: '🖓',  # thumbbackdwn    → U+1F593
    0xF03E: '🖘',  # handptlft1      → U+1F598 Misc Symbols
    0xF03F: '🖙',  # handptrt1       → U+1F599 Misc Symbols
    0xF040: '🖚',  # handptlftsld1   → U+1F59A
    0xF041: '🖛',  # handptrtsld1    → U+1F59B
    0xF042: '👈',  # handbckptleft   → U+1F448 Misc Symbols
    0xF043: '👉',  # handbckptright  → U+1F449 Misc Symbols
    0xF044: '🖜',  # handptlftsld    → U+1F59C
    0xF045: '🖝',  # handptrtsld     → U+1F59D
    0xF046: '🖞',  # handptup1       → U+1F59E Misc Symbols
    0xF047: '🖟',  # handptdwn1      → U+1F59F Misc Symbols
    0xF048: '🖠',  # handptupsld1    → U+1F5A0
    0xF049: '🖡',  # handptdwnsld1   → U+1F5A1
    0xF04A: '👆',  # handbckptup     → U+1F446
    0xF04B: '👇',  # handbckptdwn    → U+1F447
    0xF04C: '🖢',  # handptupsld     → U+1F5A2
    0xF04D: '🖣',  # handptdwnsld    → U+1F5A3
    0xF04E: '🖑',  # handspreadback  → U+1F591
    0xF04F: '🗴',  # xmark           → U+1F5F4 Dingbats
    0xF050: '✓',  # check           → U+2713 Dingbats
    0xF051: '🗵',  # boxxmark        → U+1F5F5 Misc Symbols
    0xF052: '☑',  # boxcheck        → U+2611 Misc Symbols
    0xF053: '☒',  # boxx            → U+2612
    0xF054: '☒',  # boxxbld         → U+2612
    0xF055: '⮾',  # circlex         → U+2BBE
    0xF056: '⮿',  # circlexbld      → U+2BBF
    0xF057: '⦸',  # prohibit        → U+29B8
    0xF058: '⦸',  # prohibitbld     → U+29B8
    0xF059: '🙱',  # ampersanditaldm                → U+1F671
    0xF05A: '🙴',  # ampersandbld                   → U+1F674
    0xF05B: '🙲',  # ampersandsans                  → U+1F672
    0xF05C: '🙳',  # ampersandsandm                 → U+1F673
    0xF05D: '‽',  # interrobang     → U+203D
    0xF05E: '🙹',  # interrobangdm   → U+1F679
    0xF05F: '🙺',  # interrobangsans → U+1F67A
    0xF060: '🙻',  # interrobngsandm → U+1F67B
    0xF061: '🙦',  # budleafboldne   → U+1F666
    0xF062: '🙤',  # budleafboldnw   → U+1F664
    0xF063: '🙥',  # budleafboldsw   → U+1F665
    0xF064: '🙧',  # budleafboldse   → U+1F667
    0xF065: '🙚',  # vineleafne      → U+1F65A
    0xF066: '🙘',  # vineleafnw      → U+1F658
    0xF067: '🙙',  # vineleafsw      → U+1F659
    0xF068: '🙛',  # vineleafse      → U+1F65B
    0xF069: '⓪',  # zero                           → U+24EA
    0xF06A: '①',  # one                            → U+2460
    0xF06B: '②',  # two                            → U+2461
    0xF06C: '③',  # three                          → U+2462
    0xF06D: '④',  # four                           → U+2463
    0xF06E: '⑤',  # five                           → U+2464
    0xF06F: '⑥',  # six                            → U+2465
    0xF070: '⑦',  # seven                          → U+2466
    0xF071: '⑧',  # eight                          → U+2467
    0xF072: '⑨',  # nine                           → U+2468
    0xF073: '⑩',  # ten                            → U+2469
    0xF074: '⓿',  # zeroinv         → U+24FF Enclosed Alphanumerics
    0xF075: '❶',  # oneinv          → U+2776 Dingbats
    0xF076: '❷',  # twoinv          → U+2777 Dingbats
    0xF077: '❸',  # threeinv        → U+2778 Dingbats
    0xF078: '❹',  # fourinv         → U+2779 Dingbats
    0xF079: '❺',  # fiveinv         → U+277A Dingbats
    0xF07A: '❻',  # sixinv          → U+277B Dingbats
    0xF07B: '❼',  # seveninv        → U+277C Dingbats
    0xF07C: '❽',  # eightinv        → U+277D Dingbats
    0xF07D: '❾',  # nineinv         → U+277E Dingbats
    0xF07E: '❿',  # teninv          → U+277F Dingbats
    0xF080: '☉',  # sun             → U+2609 Misc Symbols
    0xF081: '🌕',  # moonfull        → U+1F315
    0xF082: '☽',  # moonfirstqrtr   → U+263D
    0xF083: '☾',  # moonlastqrtr    → U+263E
    0xF084: '⸿',  # capitulum       → U+2E3F
    0xF085: '✝',  # cross           → U+271D Dingbats
    0xF086: '🕇',  # crossbld        → U+1F547
    0xF087: '🕜',  # onethirty       → U+1F55C
    0xF088: '🕝',  # twothirty       → U+1F55D
    0xF089: '🕞',  # threethirty     → U+1F55E
    0xF08A: '🕟',  # fourthirty      → U+1F55F
    0xF08B: '🕠',  # fivethirty      → U+1F560
    0xF08C: '🕡',  # sixthirty       → U+1F561
    0xF08D: '🕢',  # seventhirty     → U+1F562
    0xF08E: '🕣',  # eightthirty     → U+1F563
    0xF08F: '🕤',  # ninethirty      → U+1F564
    0xF090: '🕥',  # tenthirty       → U+1F565
    0xF091: '🕦',  # eleventhirty    → U+1F566
    0xF092: '🕧',  # twelvethirty    → U+1F567
    0xF093: '🙨',  # quiltsquare     → U+1F668 Geometric Shapes
    0xF094: '🙩',  # quiltsquareinv  → U+1F669
    0xF095: '•',  # circle1         → U+2022 Geometric Shapes
    0xF096: '●',  # circle3         → U+25CF
    0xF097: '⚫',  # circle5         → U+26AB
    0xF098: '⬤',  # circle7         → U+2B24
    0xF099: '🞅',  # ring1           → U+1F785
    0xF09A: '🞆',  # ring3           → U+1F786
    0xF09B: '🞇',  # ring5           → U+1F787
    0xF09C: '🞈',  # ring7           → U+1F788
    0xF09D: '🞊',  # ringbutton1     → U+1F78A
    0xF09E: '⦿',  # ringbutton3     → U+29BF
    0xF09F: '◾',  # square1         → U+25FE Geometric Shapes
    0xF0A0: '■',  # square3         → U+25A0
    0xF0A1: '◼',  # square5         → U+25FC
    0xF0A2: '⬛',  # square7         → U+2B1B
    0xF0A3: '⬜',  # box1            → U+2B1C Dingbats
    0xF0A4: '🞑',  # box5            → U+1F791
    0xF0A5: '🞒',  # box6            → U+1F792
    0xF0A6: '🞓',  # box7            → U+1F793
    0xF0A7: '🞔',  # boxbutton1      → U+1F794
    0xF0A8: '▣',  # boxbutton2      → U+25A3
    0xF0A9: '🞕',  # boxbutton3      → U+1F795
    0xF0AA: '🞖',  # boxtarget       → U+1F796
    0xF0AB: '🞗',  # rhombus1        → U+1F797 Geometric Shapes
    0xF0AC: '⬩',  # rhombus2        → U+2B29
    0xF0AD: '⬥',  # rhombus3        → U+2B25
    0xF0AE: '◆',  # rhombus5        → U+25C6 Dingbats
    0xF0AF: '◇',  # rhombopen       → U+25C7
    0xF0B0: '🞚',  # rhombbutton1    → U+1F79A
    0xF0B1: '◈',  # rhombbutton2    → U+25C8
    0xF0B2: '🞛',  # rhombbutton3    → U+1F79B
    0xF0B3: '🞜',  # rhombtarget     → U+1F79C Misc Symbols and Arrows
    0xF0B4: '🞝',  # lozenge1        → U+1F79D Geometric Shapes
    0xF0B5: '⬪',  # lozenge2        → U+2B2A
    0xF0B6: '⬧',  # lozenge3        → U+2B27
    0xF0B7: '⧫',  # lozenge5        → U+29EB
    0xF0B8: '◊',  # lozengeopen     → U+25CA
    0xF0B9: '🞠',  # lozengebutton   → U+1F7A0
    0xF0BA: '◖',  # semicircleleft  → U+25D6 Geometric Shapes
    0xF0BB: '◗',  # semicirclert    → U+25D7
    0xF0BC: '⯊',  # semicircleup    → U+2BCA
    0xF0BD: '⯋',  # semicircledwn   → U+2BCB
    0xF0BE: '◼',  # squarecent      → U+25FC
    0xF0BF: '⬥',  # rhombuscent     → U+2B25
    0xF0C0: '⬟',  # pentagon1cent   → U+2B1F Misc Symbols and Arrows
    0xF0C1: '⯂',  # pentagon2cent   → U+2BC2 Misc Symbols and Arrows
    0xF0C2: '⬣',  # hexagon1cent    → U+2B23
    0xF0C3: '⬢',  # hexagon2cent    → U+2B22
    0xF0C4: '⯃',  # octagon1        → U+2BC3
    0xF0C5: '⯄',  # octagon2        → U+2BC4
    0xF0C6: '🞡',  # cross1          → U+1F7A1 Dingbats
    0xF0C7: '🞢',  # cross2          → U+1F7A2
    0xF0C8: '🞣',  # cross3          → U+1F7A3
    0xF0C9: '🞤',  # cross4          → U+1F7A4
    0xF0CA: '🞥',  # cross5          → U+1F7A5
    0xF0CB: '🞦',  # cross6          → U+1F7A6
    0xF0CC: '🞧',  # cross7          → U+1F7A7
    0xF0CD: '🞨',  # x1              → U+1F7A8 Dingbats
    0xF0CE: '🞩',  # x2              → U+1F7A9
    0xF0CF: '🞪',  # x3              → U+1F7AA
    0xF0D0: '🞫',  # x4              → U+1F7AB
    0xF0D1: '🞬',  # x5              → U+1F7AC
    0xF0D2: '🞭',  # x6              → U+1F7AD
    0xF0D3: '🞮',  # x7              → U+1F7AE
    0xF0D4: '🞯',  # pentasterisk1   → U+1F7AF Dingbats
    0xF0D5: '🞰',  # pentasterisk2   → U+1F7B0
    0xF0D6: '🞱',  # pentasterisk3   → U+1F7B1
    0xF0D7: '🞲',  # pentasterisk4   → U+1F7B2
    0xF0D8: '🞳',  # pentasterisk5   → U+1F7B3
    0xF0D9: '🞴',  # pentasterisk6   → U+1F7B4
    0xF0DA: '🞵',  # hexasterisk1    → U+1F7B5 Dingbats
    0xF0DB: '🞶',  # hexasterisk2    → U+1F7B6
    0xF0DC: '🞷',  # hexasterisk3    → U+1F7B7
    0xF0DD: '🞸',  # hexasterisk4    → U+1F7B8
    0xF0DE: '🞹',  # hexasterisk5    → U+1F7B9
    0xF0DF: '🞺',  # hexasterisk6    → U+1F7BA
    0xF0E0: '🞻',  # octasterisk1    → U+1F7BB Dingbats
    0xF0E1: '🞼',  # octasterisk2    → U+1F7BC
    0xF0E2: '🞽',  # octasterisk3    → U+1F7BD
    0xF0E3: '🞾',  # octasterisk4    → U+1F7BE
    0xF0E4: '🞿',  # octasterisk5    → U+1F7BF
    0xF0E5: '🟀',  # tristar1        → U+1F7C0 Dingbats
    0xF0E6: '🟂',  # tristar3        → U+1F7C2
    0xF0E7: '🟄',  # crosstar1       → U+1F7C4 Dingbats
    0xF0E8: '✦',  # crosstar3       → U+2726
    0xF0E9: '🟉',  # pentastar1      → U+1F7C9 Misc Symbols
    0xF0EA: '★',  # pentastar3      → U+2605
    0xF0EB: '✶',  # hexstar1        → U+2736 Dingbats
    0xF0EC: '🟋',  # hexstar3        → U+1F7CB
    0xF0ED: '✷',  # octastar1       → U+2737 Dingbats
    0xF0EE: '🟏',  # octastar3       → U+1F7CF
    0xF0EF: '🟒',  # dodecastar1     → U+1F7D2 Dingbats
    0xF0F0: '✹',  # dodecastar2     → U+2739
    0xF0F1: '🟃',  # tristar4        → U+1F7C3
    0xF0F2: '🟇',  # crosstar4       → U+1F7C7
    0xF0F3: '✯',  # pentastar4      → U+272F
    0xF0F4: '🟍',  # hexstar4        → U+1F7CD
    0xF0F5: '🟔',  # dodecastar4     → U+1F7D4
    0xF0F6: '⯌',  # cusp            → U+2BCC Geometric Shapes
    0xF0F7: '⯍',  # cusp1           → U+2BCD
    0xF0F8: '※',  # xdot            → U+203B
    0xF0F9: '⁂',  # trihexasterisk  → U+2042 Dingbats
}


# Wingdings3 字体完整映射表 (0xF020-0xF0F0)
#https://www.alanwood.net/demos/wingdings-3.html
#注意：上面的文档使用的范围为0x20-0xff，转化为0xf020-0xff
WINGDINGS3_MAP = {
    0xF020: ' ',   # space
    0xF021: '⭠',  # a2left          → U+2B60 Arrows
    0xF022: '⭢',  # a2right         → U+2B62
    0xF023: '⭡',  # a2up            → U+2B61
    0xF024: '⭣',  # a2down          → U+2B63
    0xF025: '⭦',  # a2nw            → U+2B66
    0xF026: '⭧',  # a2ne            → U+2B67
    0xF027: '⭩',  # a2sw            → U+2B69
    0xF028: '⭨',  # a2se            → U+2B68
    0xF029: '⭰',  # a2tableft       → U+2B70 Arrows
    0xF02A: '⭲',  # a2tabright      → U+2B72
    0xF02B: '⭱',  # a2tabup         → U+2B71
    0xF02C: '⭳',  # a2tabdown       → U+2B73
    0xF02D: '⭶',  # a2home          → U+2B76
    0xF02E: '⭸',  # a2end           → U+2B78
    0xF02F: '⭻',  # a2pageup        → U+2B7B
    0xF030: '⭽',  # a2pagedown      → U+2B7D
    0xF031: '⭤',  # a2leftright     → U+2B64
    0xF032: '⭥',  # a2updown        → U+2B65
    0xF033: '⭪',  # a2leftdash      → U+2B6A
    0xF034: '⭬',  # a2rightdash     → U+2B6C
    0xF035: '⭫',  # a2updash        → U+2B6B
    0xF036: '⭭',  # a2downdash      → U+2B6D
    0xF037: '⭍',  # a2zigzag        → U+2B4D
    0xF038: '⮠',  # a2cornerdwnleft → U+2BA0
    0xF039: '⮡',  # a2cornerdwnrt   → U+2BA1
    0xF03A: '⮢',  # a2cornerupleft  → U+2BA2
    0xF03B: '⮣',  # a2corneruprt    → U+2BA3
    0xF03C: '⮤',  # a2cornerleftup  → U+2BA4
    0xF03D: '⮥',  # a2cornerrightup → U+2BA5
    0xF03E: '⮦',  # a2cornerleftdwn → U+2BA6
    0xF03F: '⮧',  # a2cornerrtdwn   → U+2BA7
    0xF040: '⮐',  # returnleft      → U+2B90
    0xF041: '⮑',  # returnright     → U+2B91
    0xF042: '⮒',  # newlineleft     → U+2B92
    0xF043: '⮓',  # newlineright    → U+2B93
    0xF044: '⮀',  # a2opleftrt      → U+2B80
    0xF045: '⮃',  # a2opdwnup       → U+2B83
    0xF046: '⭾',  # a2optableftrt   → U+2B7E
    0xF047: '⭿',  # a2optabdwnup    → U+2B7F
    0xF048: '⮄',  # a2parllleft     → U+2B84
    0xF049: '⮆',  # a2parllright    → U+2B86
    0xF04A: '⮅',  # a2parllup       → U+2B85
    0xF04B: '⮇',  # a2parlldown     → U+2B87
    0xF04C: '⮏',  # a2uleftdown     → U+2B8F
    0xF04D: '⮍',  # a2urightup      → U+2B8D
    0xF04E: '⮎',  # a2ubelowright   → U+2B8E
    0xF04F: '⮌',  # a2uaboveleft    → U+2B8C
    0xF050: '⭮',  # a2clockwise     → U+2B6E
    0xF051: '⭯',  # a2cntrclockwise → U+2B6F
    0xF052: '⎋',  # escape1         → U+238B
    0xF053: '⌤',  # enter           → U+2324
    0xF054: '⌃',  # control         → U+2303
    0xF055: '⌥',  # option          → U+2325
    0xF056: '⎵',  # spacemark       → U+23B5
    0xF057: '⍽',  # nobreakspacemark→ U+237D
    0xF058: '⇪',  # shiftlock       → U+21EA
    0xF059: '⮸',  # capslock        → U+2BB8
    0xF05A: '🢠',  # oleftshadow     → U+1F8A0 Geometric Shapes
    0xF05B: '🢡',  # orightshadow    → U+1F8A1
    0xF05C: '🢢',  # oleftshadup     → U+1F8A2
    0xF05D: '🢣',  # orightshadup    → U+1F8A3
    0xF05E: '🢤',  # oleftshadlft    → U+1F8A4
    0xF05F: '🢥',  # orightshadrt    → U+1F8A5
    0xF060: '🢦',  # oleftshadrt     → U+1F8A6
    0xF061: '🢧',  # orightshadlft   → U+1F8A7
    0xF062: '🢨',  # oleftoblqshad   → U+1F8A8
    0xF063: '🢩',  # orightoblqshad  → U+1F8A9
    0xF064: '🢪',  # oleftoblqshadup → U+1F8AA
    0xF065: '🢫',  # orightoblqshadup→ U+1F8AB
    0xF066: '←',  # b2left          → U+2190 Arrows
    0xF067: '→',  # b2right         → U+2192
    0xF068: '↑',  # b2up            → U+2191
    0xF069: '↓',  # b2down          → U+2193
    0xF06A: '↖',  # b2nw            → U+2196
    0xF06B: '↗',  # b2ne            → U+2197
    0xF06C: '↙',  # b2sw            → U+2199
    0xF06D: '↘',  # b2se            → U+2198
    0xF06E: '🡘',  # b2leftright     → U+1F858
    0xF06F: '🡙',  # b2updown        → U+1F859
    0xF070: '▲',  # triangleup      → U+25B2 Geometric Shapes
    0xF071: '▼',  # triangledwn     → U+25BC
    0xF072: '△',  # triangleopenup  → U+25B3
    0xF073: '▽',  # triangleopendwn → U+25BD
    0xF074: '◄',  # triangleleft    → U+25C4
    0xF075: '►',  # trianglert      → U+25BA
    0xF076: '◁',  # triangleopenleft→ U+25C1
    0xF077: '▷',  # triangleopenrt  → U+25B7
    0xF078: '◣',  # triang45baseleft→ U+25E3
    0xF079: '◢',  # triangle45basert→ U+25E2
    0xF07A: '◤',  # triangle45toppleft→ U+25E4
    0xF07B: '◥',  # triangle45toprt → U+25E5
    0xF07C: '🞀',  # triangle45left  → U+1F780
    0xF07D: '🞂',  # triangle45right → U+1F782
    0xF07E: '🞁',  # triangle45up    → U+1F781
    0xF080: '🞃',  # triangle45down  → U+1F783
    0xF081: '▲',  # trianglecentup  → U+25B2
    0xF082: '▼',  # trianglecentdwn → U+25BC
    0xF083: '◀',  # trianglecentleft→ U+25C0
    0xF084: '▶',  # trianglecentrt  → U+25B6
    0xF085: '⮜',  # headleft        → U+2B9C Arrows
    0xF086: '⮞',  # headright       → U+2B9E
    0xF087: '⮝',  # headup          → U+2B9D
    0xF088: '⮟',  # headdown        → U+2B9F
    0xF089: '🠐',  # c1left          → U+1F810
    0xF08A: '🠒',  # c1right         → U+1F812
    0xF08B: '🠑',  # c1up            → U+1F811
    0xF08C: '🠓',  # c1down          → U+1F813
    0xF08D: '🠔',  # c2left          → U+1F814
    0xF08E: '🠖',  # c2right         → U+1F816
    0xF08F: '🠕',  # c2up            → U+1F815
    0xF090: '🠗',  # c2down          → U+1F817
    0xF091: '🠘',  # c3left          → U+1F818
    0xF092: '🠚',  # c3right         → U+1F81A
    0xF093: '🠙',  # c3up            → U+1F819
    0xF094: '🠛',  # c3down          → U+1F81B
    0xF095: '🠜',  # c4left          → U+1F81C
    0xF096: '🠞',  # c4right         → U+1F81E
    0xF097: '🠝',  # c4up            → U+1F81D
    0xF098: '🠟',  # c4down          → U+1F81F
    0xF099: '🠀',  # a1left          → U+1F800
    0xF09A: '🠂',  # a1right         → U+1F802
    0xF09B: '🠁',  # a1up            → U+1F801
    0xF09C: '🠃',  # a1down          → U+1F803
    0xF09D: '🠄',  # a3left          → U+1F804
    0xF09E: '🠆',  # a3right         → U+1F806
    0xF09F: '🠅',  # a3up            → U+1F805
    0xF0A0: '🠇',  # a3down          → U+1F807
    0xF0A1: '🠈',  # a4left          → U+1F808
    0xF0A2: '🠊',  # a4right         → U+1F80A
    0xF0A3: '🠉',  # a4up            → U+1F809
    0xF0A4: '🠋',  # a4down          → U+1F80B
    0xF0A5: '🠠',  # d1left          → U+1F820
    0xF0A6: '🠢',  # d1right         → U+1F822
    0xF0A7: '🠤',  # e3left          → U+1F824
    0xF0A8: '🠦',  # e3right         → U+1F826
    0xF0A9: '🠨',  # f4left          → U+1F828
    0xF0AA: '🠪',  # f4right         → U+1F82A
    0xF0AB: '🠬',  # g5left          → U+1F82C
    0xF0AC: '🢜',  # g51             → U+1F89C
    0xF0AD: '🢝',  # g5dash2         → U+1F89D
    0xF0AE: '🢞',  # g5dash3         → U+1F89E
    0xF0AF: '🢟',  # g5dash4         → U+1F89F
    0xF0B0: '🠮',  # g5right         → U+1F82E
    0xF0B1: '🠰',  # h6left          → U+1F830
    0xF0B2: '🠲',  # h6right         → U+1F832
    0xF0B3: '🠴',  # i8left          → U+1F834
    0xF0B4: '🠶',  # i8right         → U+1F836
    0xF0B5: '🠸',  # j6left          → U+1F838
    0xF0B6: '🠺',  # j6right         → U+1F83A
    0xF0B7: '🠹',  # j6up            → U+1F839
    0xF0B8: '🠻',  # j6down          → U+1F83B
    0xF0B9: '🢘',  # k6left          → U+1F898
    0xF0BA: '🢚',  # k6right         → U+1F89A
    0xF0BB: '🢙',  # k6up            → U+1F899
    0xF0BC: '🢛',  # k6down          → U+1F89B
    0xF0BD: '🠼',  # l7left          → U+1F83C
    0xF0BE: '🠾',  # l7right         → U+1F83E
    0xF0BF: '🠽',  # l7up            → U+1F83D
    0xF0C0: '🠿',  # l7down          → U+1F83F
    0xF0C1: '🡀',  # m9left          → U+1F840
    0xF0C2: '🡂',  # m9right         → U+1F842
    0xF0C3: '🡁',  # m9up            → U+1F841
    0xF0C4: '🡃',  # m9down          → U+1F843
    0xF0C5: '🡄',  # n7left          → U+1F844
    0xF0C6: '🡆',  # n7right         → U+1F846
    0xF0C7: '🡅',  # n7up            → U+1F845
    0xF0C8: '🡇',  # n7down          → U+1F847
    0xF0C9: '⮨',  # arrowdwnleft    → U+2BA8
    0xF0CA: '⮩',  # arrowdwnrt      → U+2BA9
    0xF0CB: '⮪',  # arrowupleft     → U+2BAA
    0xF0CC: '⮫',  # arrowuprt       → U+2BAB
    0xF0CD: '⮬',  # arrowleftup     → U+2BAC
    0xF0CE: '⮭',  # arrowrtup       → U+2BAD
    0xF0CF: '⮮',  # arrowleftdwn    → U+2BAE
    0xF0D0: '⮯',  # arrowrtdwn      → U+2BAF
    0xF0D1: '🡠',  # barb1left       → U+1F860
    0xF0D2: '🡢',  # barb1right      → U+1F862
    0xF0D3: '🡡',  # barb1up         → U+1F861
    0xF0D4: '🡣',  # barb1down       → U+1F863
    0xF0D5: '🡤',  # barb1nw         → U+1F864
    0xF0D6: '🡥',  # barb1ne         → U+1F865
    0xF0D7: '🡧',  # barb1sw         → U+1F867
    0xF0D8: '🡦',  # barb1se         → U+1F866
    0xF0D9: '🡰',  # barb3left       → U+1F870
    0xF0DA: '🡲',  # barb3right      → U+1F872
    0xF0DB: '🡱',  # barb3up         → U+1F871
    0xF0DC: '🡳',  # barb3down       → U+1F873
    0xF0DD: '🡴',  # barb3nw         → U+1F874
    0xF0DE: '🡵',  # barb3ne         → U+1F875
    0xF0DF: '🡷',  # barb3sw         → U+1F877
    0xF0E0: '🡶',  # barb3se         → U+1F876
    0xF0E1: '🢀',  # barb5left       → U+1F880
    0xF0E2: '🢂',  # barb5right      → U+1F882
    0xF0E3: '🢁',  # barb5up         → U+1F881
    0xF0E4: '🢃',  # barb5down       → U+1F883
    0xF0E5: '🢄',  # barb5nw         → U+1F884
    0xF0E6: '🢅',  # barb5ne         → U+1F885
    0xF0E7: '🢇',  # barb5sw         → U+1F887
    0xF0E8: '🢆',  # barb5se         → U+1F886
    0xF0E9: '🢐',  # drawleft        → U+1F890
    0xF0EA: '🢒',  # drawright       → U+1F892
    0xF0EB: '🢑',  # drawup          → U+1F891
    0xF0EC: '🢓',  # drawdown        → U+1F893
    0xF0ED: '🢔',  # drawdblleft     → U+1F894
    0xF0EE: '🢖',  # drawdblright    → U+1F896
    0xF0EF: '🢕',  # drawdblup       → U+1F895
    0xF0F0: '🢗',  # drawdbldown     → U+1F897
}

#TODO 有些相近的，为了简化，可能都是对应同一个unicode，反过来，一个unicode对应多个pua
REVERSED_WINGDINGS_MAP={v:k for k,v in WINGDINGS_MAP.items()}
REVERSED_WINGDINGS2_MAP={v:k for k,v in WINGDINGS2_MAP.items()}
REVERSED_WINGDINGS3_MAP={v:k for k,v in WINGDINGS3_MAP.items()}

"""
对于wingdings，默认转化为标准的unicode，但是通过char.pdf_text='' 保留pua字符，原因：
1.使用标准的unicode，只要当前系统安装了支持的字体，就可以显示，虽然对于0x2B00-0x2BFF，很多系统自带的字体还不支持，
  但是可以通过下载安装NotoSansSymbols2-Regular.ttf即可。不需要做任何设置。
   因为mac/windows，当第一个字体没有对应的glhpy，会查找第二个字体，如果是pua的，就不查找的，因为为私有区域，各个字体实现不同。

2. 在html中，只要安装了Noto Sans Symbols 2，或者其他的支持这个范围的字体文件，就可以。
   当然，友好一点是指定网络字体（但是需要服务器，指定google fonts经常访问不了）。
   对于标准字体，系统会查找所有的字体文件，直到最后。

   对于私有区域的字符，就必须指定font-family（在这个列表中的会自动查找全部），因为系统中的遇到私有字体，就不会再继续查找下一个了。

"""
class WingdingsRecognizer:
    _SPACE_CODE: Final = 0xF020
    _SQUARE_CODES: Final = (
        0xF06E,
        0xF06F,
        0xF070,
        0xF071,
        0xF072,
        0xF0A0,
        0xF0A8,
    )

    def __init__(self, font_path: str|Path, font_size: int =32):
        self._font_path = Path(font_path)
        self._font_size = font_size
        self._templates: dict[int, np.ndarray] = {}
        self._phashes: dict[int, int] = {}
        self._font = ImageFont.truetype(self._font_path, self._font_size, encoding='symb')
        self._build_templates()

    def _get_codepoints(self):
        from fontTools.ttLib import TTFont
        font = TTFont(self._font_path)

        # 获取 cmap 表（码位 → 字形 映射）
        #(3,0) => 返回的是0xf020->0xf0ff
        #(1,0) => 0x20-0x9d
        cmap = font.getBestCmap([(3,0)])
        results:list[int] = []
        for codepoint, glyph_name in cmap.items():
            #print((hex(codepoint),glyph_name))
            results.append(codepoint)
        results.sort()
        return results

    def _render_char(self, char: str) -> np.ndarray | None:
        sz = self._font_size
        img = Image.new("L", (sz * 2, sz * 2), 255)
        ImageDraw.Draw(img).text((sz // 2, sz // 2), char, font=self._font, fill=0)
        return self._crop(np.array(img))

    def _crop(self, arr: np.ndarray) -> np.ndarray | None:
        """去除白边，返回紧裁剪后的灰度图；若全白返回 None。"""
        coords = cv2.findNonZero(255 - arr)
        if coords is None:
            return None
        x, y, w, h = cv2.boundingRect(coords)
        return arr[y:y + h, x:x + w]

    def _normalize(self, img: np.ndarray, size: int = 32) -> np.ndarray:
        """缩放到固定尺寸，二值化，去噪。"""
        resized = cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)
        _, binary = cv2.threshold(resized, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return binary

    def _phash(self, img: np.ndarray) -> int:
        norm = self._normalize(img, 32)
        dct = cv2.dct(np.float32(norm))
        low = dct[:8, :8].flatten()
        mean = low.mean()
        return int(sum(1 << i for i, v in enumerate(low) if v > mean))

    def _build_templates(self):
        for code in self._get_codepoints():
            tmpl = self._render_char(chr(code))
            if tmpl is None:
                continue
            self._templates[code] = tmpl
            self._phashes[code] = self._phash(tmpl)

    def _preprocess_patch(self, patch: np.ndarray) -> np.ndarray | None:
        """对输入 patch 做与模板相同的预处理：灰度→裁白边。"""
        if len(patch.shape) == 3:
            patch = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
        return self._crop(patch)

    def _is_blank_patch(self, patch: np.ndarray) -> bool:
        """判断 patch 中是否没有可识别的墨迹。"""
        if patch.size == 0:
            return True
        if len(patch.shape) == 3:
            patch = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)

        min_value = int(np.min(patch))
        max_value = int(np.max(patch))
        if max_value - min_value <= 8:
            return True

        background = float(np.percentile(patch, 95))
        ink = patch < max(0.0, background - 24.0)
        return float(np.count_nonzero(ink) / patch.size) < 0.002

    def _match_template(self, patch: np.ndarray) -> int | None:
        p = self._preprocess_patch(patch)
        if p is None:
            return None
        pn = self._normalize(p)
        best_char, best_score = None, 0.5  # 降低阈值从 0.6 到 0.5
        for code, tmpl in self._templates.items():
            tn = self._normalize(tmpl)
            score = float(cv2.matchTemplate(pn, tn, cv2.TM_CCOEFF_NORMED).max())
            if score > best_score:
                best_score, best_char = score, code
        return best_char

    def _match_phash(self, patch: np.ndarray) -> int | None:
        p = self._preprocess_patch(patch)
        if p is None:
            return None
        h = self._phash(p)
        best_char, best_dist = None, 64
        for code, ph in self._phashes.items():
            dist = bin(h ^ ph).count('1')
            if dist < best_dist:
                best_dist, best_char = dist, code
        return best_char

    def _match_square_shape(self, patch: np.ndarray) -> int | None:
        p = self._preprocess_patch(patch)
        if p is None:
            return None

        mask = self._foreground_mask(p)
        if mask is None:
            return None
        h, w = mask.shape[:2]
        if h < 6 or w < 6:
            return None
        aspect = w / h
        if not 0.55 <= aspect <= 1.8:
            return None

        black_ratio = float(np.count_nonzero(mask) / mask.size)
        hole_ratio = self._hole_ratio(mask)
        center = mask[
            h // 4 : max(h // 4 + 1, 3 * h // 4),
            w // 4 : max(w // 4 + 1, 3 * w // 4),
        ]
        center_black_ratio = float(np.count_nonzero(center) / center.size)

        is_solid_square = black_ratio >= 0.68 and hole_ratio < 0.08
        is_hollow_square = hole_ratio >= 0.18 and center_black_ratio <= 0.25
        if not is_solid_square and not is_hollow_square:
            return None

        candidates = [
            code
            for code in self._SQUARE_CODES
            if code in self._templates and (code != 0xF0A0 or is_solid_square)
        ]
        if is_hollow_square:
            candidates = [
                code for code in candidates if code != 0xF06E and code != 0xF0A0
            ]
        if not candidates:
            return None

        patch_features = self._square_features(mask)
        patch_norm = self._normalize(p, 48)
        best_code: int | None = None
        best_score = float("inf")
        for code in candidates:
            tmpl = self._templates[code]
            tmpl_mask = self._foreground_mask(tmpl)
            if tmpl_mask is None:
                continue
            tmpl_features = self._square_features(tmpl_mask)
            tmpl_norm = self._normalize(tmpl, 48)
            mse = float(
                np.mean(
                    (patch_norm.astype(np.float32) - tmpl_norm.astype(np.float32))
                    ** 2
                )
            ) / (255 * 255)
            feature_score = sum(
                abs(a - b) * weight
                for a, b, weight in zip(
                    patch_features,
                    tmpl_features,
                    (1.2, 2.0, 0.8, 0.5, 0.5),
                )
            )
            score = mse + feature_score
            if score < best_score:
                best_score = score
                best_code = code
        return best_code

    def _foreground_mask(self, img: np.ndarray) -> np.ndarray | None:
        if len(img.shape) == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        coords = cv2.findNonZero(mask)
        if coords is None:
            return None
        x, y, w, h = cv2.boundingRect(coords)
        return mask[y : y + h, x : x + w]

    def _hole_ratio(self, mask: np.ndarray) -> float:
        h, w = mask.shape[:2]
        white = (mask == 0).astype(np.uint8)
        flood = white.copy()
        flood_mask = np.zeros((h + 2, w + 2), np.uint8)
        for x in range(w):
            if flood[0, x]:
                cv2.floodFill(flood, flood_mask, (x, 0), 0)
            if flood[h - 1, x]:
                cv2.floodFill(flood, flood_mask, (x, h - 1), 0)
        for y in range(h):
            if flood[y, 0]:
                cv2.floodFill(flood, flood_mask, (0, y), 0)
            if flood[y, w - 1]:
                cv2.floodFill(flood, flood_mask, (w - 1, y), 0)
        return float(np.count_nonzero(flood) / mask.size)

    def _square_features(
        self, mask: np.ndarray
    ) -> tuple[float, float, float, float, float]:
        h, w = mask.shape[:2]
        black_ratio = float(np.count_nonzero(mask) / mask.size)
        hole_ratio = self._hole_ratio(mask)
        aspect = w / h
        right = mask[:, max(0, int(w * 0.75)) :]
        bottom = mask[max(0, int(h * 0.75)) :, :]
        right_ratio = float(np.count_nonzero(right) / right.size) if right.size else 0.0
        bottom_ratio = float(np.count_nonzero(bottom) / bottom.size) if bottom.size else 0.0
        return black_ratio, hole_ratio, aspect, right_ratio, bottom_ratio

    def recognize_patch(self, patch: np.ndarray, method: str = "vote") -> int | None:
        if self._is_blank_patch(patch):
            return self._SPACE_CODE

        if method == "template":
            return self._match_template(patch)
        elif method == "phash":
            return self._match_phash(patch)
        # vote: template 优先，phash 作为备选
        t_result = self._match_template(patch)
        s_result = self._match_square_shape(patch)
        p_result = self._match_phash(patch)

        verbose=0

        if verbose:
            print('=============>>>')
            print(f"template: {hex(t_result) if t_result else None}")
            print(f"square:   {hex(s_result) if s_result else None}")
            print(f"phash:    {hex(p_result) if p_result else None}")

        # 策略：template 优先；如果几何特征明确是方框而 template 命中非方框，使用方框结果。


        from collections import Counter
        results:list[int]=[]
        if t_result is not None:
            results.append(t_result)
        if s_result is not None:
            results.append(s_result)
        if p_result is not None:
            results.append(p_result)

        if len(results)>0 and (item:=Counter(results).most_common()[0]) and item[1]>1:
            result=item[0]
            if verbose:
                print(f"选择 次数最多的: {hex(result)} {chr(result)} {wingdings2standard('wingdings',chr(result))}")
        elif t_result is not None and (
            s_result is None or t_result in self._SQUARE_CODES
        ):
            result = t_result
            if verbose:
                print(f"选择 template: {hex(result)} {chr(result)} {wingdings2standard('wingdings',chr(result))}")
        elif s_result is not None:
            result = s_result
            if verbose:
                print(f"选择 square: {hex(result)} {chr(result)} {wingdings2standard('wingdings',chr(result))}")
        elif p_result is not None:
            result = p_result
            if verbose:
                print(f"选择 phash: {hex(result)} {chr(result)} {wingdings2standard('wingdings',chr(result))}")
        else:
            result = None
            if verbose:
                print("无法识别")
        if verbose>1:
            cv2.imshow('img',patch)
            cv2.waitKey()
        return result

    def recognize(self, img: np.ndarray, bboxes: Sequence[Sequence[float]], method: str = "vote") -> list[str|None]:
        if len(img.shape) == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        chars:list[str|None]=[]
        for x0,y0,x1,y1 in bboxes:
            code = self.recognize_patch(img[y0:y1,x0:x1],method)
            if code is not None:
                chars.append(chr(code))
            else:
                chars.append(None)
        return chars


    _lock:Final=threading.RLock()
    _instances:Final[dict[str,Self]]={}
    @classmethod
    def get(cls,name:str)->Self:
        with cls._lock:
            instance = cls._instances.get(name)
            if instance is None:
                from . import fonts
                dir_=Path(fonts.__file__).parent
                instance = cls(dir_/f'{name}.ttf')
                cls._instances[name]=instance
            return instance

def wingdings2standard(name:str,text:str)->str:
    """输入wingdings使用的私有域的（0xf020-0xf0ff)，转化为标准的unicode"""
    if name in ('wingdings2',):
        mapping=WINGDINGS2_MAP
    elif name in ('wingdings3',):
        mapping=WINGDINGS3_MAP
    else:
        mapping=WINGDINGS_MAP
    
    #如果没有映射，也就是不是在0xf020<=codepoint<=0xf0ff区域，返回原始值
    if len(text)==1:
        return mapping.get(ord(text),text)
    else:
        chars:list[str]=[]
        for c in text:
            c = mapping.get(ord(c),c)
            chars.append(c)
        return ''.join(chars)
    

def standard2wingdings(name:str,text:str)->str:
    """输入标准的unicode，转化为wingdings的私有域unicode"""
    if name in ('wingdings2',):
        mapping=REVERSED_WINGDINGS_MAP
    elif name in ('wingdings3',):
        mapping=REVERSED_WINGDINGS2_MAP
    else:
        mapping=REVERSED_WINGDINGS3_MAP
    
    if len(text)==1:
        return chr(mapping.get(text,ord(text)))
    else:
        chars:list[str]=[]
        for c in text:
            c = chr(mapping.get(c,ord(c)))
            chars.append(c)
        return ''.join(chars)


    
