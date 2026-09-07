package com.ronin.ai.ui.components
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
@Composable fun MessageBubble(text:String, mine:Boolean){ Box(Modifier.fillMaxWidth().padding(vertical=4.dp), if(mine) Alignment.CenterEnd else Alignment.CenterStart){Text(text,Modifier.background(if(mine) Color(0xFF5B21B6) else Color(0xFF20242D),RoundedCornerShape(16.dp)).padding(12.dp),color=Color.White,style=MaterialTheme.typography.bodyLarge)} }
