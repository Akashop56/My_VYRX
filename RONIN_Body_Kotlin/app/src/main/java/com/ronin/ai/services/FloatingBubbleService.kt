package com.ronin.ai.services

import android.app.Service
import android.content.Intent
import android.graphics.Color
import android.graphics.PixelFormat
import android.os.IBinder
import android.provider.Settings
import android.view.Gravity
import android.view.WindowManager
import android.widget.TextView
import com.ronin.ai.MainActivity

class FloatingBubbleService : Service() {
 private var view: TextView?=null; private lateinit var wm:WindowManager
 override fun onStartCommand(intent:Intent?,flags:Int,startId:Int):Int { if(!Settings.canDrawOverlays(this)) { stopSelf(); return START_NOT_STICKY }; wm=getSystemService(WINDOW_SERVICE) as WindowManager; if(view==null) { view=TextView(this).apply { text="R"; textSize=22f; setTextColor(Color.WHITE); setBackgroundColor(Color.rgb(91,33,182)); setPadding(28,16,28,16); setOnClickListener { startActivity(Intent(this@FloatingBubbleService,MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) } }; wm.addView(view,WindowManager.LayoutParams(WindowManager.LayoutParams.WRAP_CONTENT,WindowManager.LayoutParams.WRAP_CONTENT,WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,PixelFormat.TRANSLUCENT).apply { gravity=Gravity.END or Gravity.CENTER_VERTICAL }) }; return START_STICKY }
 override fun onDestroy(){ view?.let{wm.removeView(it)}; view=null; super.onDestroy() }
 override fun onBind(intent:Intent?):IBinder?=null
}
