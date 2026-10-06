#==============================================================================
# RPGM Hub Resolution (generated, "stock640" mode)
# Stock RGSS cannot go beyond 640x480 and cannot draw upscaled tilesets.
# This script only enlarges the game screen; use the mkxp-z Hires pack for real HD.
#==============================================================================
module RPGMHub
  WIDTH  = __WIDTH__
  HEIGHT = __HEIGHT__
end

begin
  if Graphics.respond_to?(:resize_screen)
    Graphics.resize_screen(RPGMHub::WIDTH, RPGMHub::HEIGHT)
  end
rescue Exception
  # an engine that refuses the size keeps its default screen
end
