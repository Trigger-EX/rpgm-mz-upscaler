# RPGM Hub: TRGSSX.dll stand-in for mkxp-z (generated).
# This game's scripts call TRGSSX.dll (Tomy's "RGSS Extension Library") through Win32API. A Windows DLL cannot load under
# mkxp-z, so the version check in the script fails ("TRGSSX.dll not found or old") and the game stops. This answers that check
# so the game starts. The drawing functions of the DLL are not reimplemented: win32_wrap.rb makes them return 0, so effects the
# DLL would draw (rotated or blended blits, polygons, anti-aliased text) are missing.
module Win32API_Impl
  module TRGSSX
    class DllGetVersion
      def call(args)
        0x7FFFFFFF
      end
    end
  end
end
